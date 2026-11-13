"""Dashboard server: serves dashboard/index.html and a small JSON API on 127.0.0.1.

Long work (stepping a job, ticking) is started as a detached `factory.py` process, so the page never blocks.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, github, lifecycle, paths, queue, reviewers, state
from .model import COLUMNS, ACTIVE
from .util import FactoryError


def job_view(st: dict, cfg: dict) -> dict:
    try:
        reviewer_list = reviewers.review_names(st, cfg)
    except FactoryError:
        reviewer_list = st.get("reviewers", [])
    keep = ["job_id", "issue", "title", "url", "priority", "queue_pos", "state", "parent", "children",
            "blocked_by", "risk", "scope", "round", "round_limit", "phase", "gate", "pr_url", "branch",
            "worktree", "labels", "reviewers_override", "updated_at", "answered", "notified"]
    view = {k: st.get(k) for k in keep}
    view["last_note"] = st["history"][-1]["note"] if st.get("history") else ""
    view["resumed_by_answer"] = view["last_note"] == "author answered the questions"
    view["reviewers"] = reviewer_list
    return view


def api_state(cfg: dict) -> dict:
    states = state.all_states()
    return {
        "columns": COLUMNS,
        "capacity": {"active": sum(1 for s in states if s["state"] in ACTIVE), "max": cfg["max_parallel_jobs"]},
        "config": {
            "max_parallel_jobs": cfg["max_parallel_jobs"], "max_parallel_subtasks": cfg["max_parallel_subtasks"],
            "max_rounds": cfg["max_rounds"], "reviewers_defaults": cfg["reviewers"]["defaults"],
            "reviewers_always": cfg["reviewers"]["always"], "auto_merge": cfg["auto_merge"],
            "target_repo": cfg.get("target_repo", ""), "base_branch": cfg["github"]["base_branch"],
        },
        "catalog": {
            "reviewers": {k: v["description"] for k, v in cfg["reviewers"]["catalog"].items()},
            "specialists": {k: v["description"] for k, v in cfg["agents"]["catalog"].items()},
        },
        "jobs": [job_view(s, cfg) for s in states],
    }


def safe_job(job_id: str) -> str:
    if not re.fullmatch(r"issue-\d+", job_id):
        raise FactoryError("bad job id")
    if not state.job_dir(job_id).exists():
        raise FactoryError(f"unknown job {job_id}")
    return job_id


def job_files(job_id: str) -> list[str]:
    d = state.job_dir(job_id)
    return sorted(str(p.relative_to(d)) for p in d.rglob("*")
                  if p.is_file() and p.suffix in {".md", ".json", ".log"} and p.name != ".lock")


def spawn(args: list[str]) -> None:
    """Run `factory.py <args>` detached, so a click returns immediately."""
    subprocess.Popen([sys.executable, str(paths.ENTRY), *args], cwd=str(paths.ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeFactory"

    def log_message(self, *a):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            cfg = config.load()
            if url.path in {"/", "/index.html"}:
                body = paths.DASHBOARD.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if url.path == "/api/state":
                with state.LOCK:
                    return self.send_json(api_state(cfg))
            m = re.fullmatch(r"/api/job/([^/]+)/files", url.path)
            if m:
                return self.send_json({"files": job_files(safe_job(m.group(1)))})
            m = re.fullmatch(r"/api/job/([^/]+)/file", url.path)
            if m:
                job_id = safe_job(m.group(1))
                rel = urllib.parse.parse_qs(url.query).get("path", [""])[0]
                p = (state.job_dir(job_id) / rel).resolve()
                if state.job_dir(job_id).resolve() not in p.parents or not p.is_file():
                    raise FactoryError("not a job file")
                return self.send_json({"path": rel, "text": p.read_text(errors="replace")})
            self.send_json({"error": "not found"}, 404)
        except FactoryError as e:
            self.send_json({"error": str(e)}, 400)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            cfg = config.load()
            data = self.body()
            if url.path == "/api/sync":
                return self.send_json({"message": queue.sync(cfg)})
            if url.path == "/api/tick":
                spawn(["tick"])
                return self.send_json({"message": "tick started in the background"})
            if url.path == "/api/config":
                for k, v in data.items():
                    config.set_value(cfg, k, v)
                return self.send_json({"message": "saved", "config": api_state(config.load())["config"]})
            if url.path == "/api/move":
                return self.send_json({"message": lifecycle.move_job(safe_job(data["job"]), data["direction"])})
            m = re.fullmatch(r"/api/job/([^/]+)/(\w+)", url.path)
            if not m:
                return self.send_json({"error": "not found"}, 404)
            job_id, action = safe_job(m.group(1)), m.group(2)
            st = state.load_state(job_id)
            if action == "step":
                spawn(["step", job_id])
                return self.send_json({"message": f"{job_id} step started in the background"})
            if action == "priority":
                lifecycle.set_priority(job_id, data["priority"])
                return self.send_json({"message": f"{job_id} priority {data['priority']}"})
            if action == "reviewers":
                names = data.get("names")
                return self.send_json({"message": "reviewers: " + ", ".join(lifecycle.set_reviewers(job_id, cfg, names))})
            if action == "feedback":
                lifecycle.feedback_job(st, cfg, data.get("text", ""))
                return self.send_json({"message": f"{job_id} back to build"})
            if action == "reject":
                lifecycle.reject_job(st, cfg, data.get("reason", ""))
                return self.send_json({"message": f"{job_id} rejected"})
            if action == "merge":
                return self.send_json({"message": lifecycle.merge_job(st, cfg, dry=False)})
            if action == "split":
                if data.get("decision") == "cancel":
                    lifecycle.cancel_split(st)
                    return self.send_json({"message": "split cancelled"})
                return self.send_json({"message": lifecycle.approve_split(st, cfg, dry=False)})
            if action == "set":
                state.set_state(st, data["state"], data.get("note", "dashboard"), force=bool(data.get("force")))
                return self.send_json({"message": f"{job_id} -> {data['state']}"})
            self.send_json({"error": f"unknown action {action}"}, 404)
        except (FactoryError, KeyError, ValueError) as e:
            self.send_json({"error": str(e)}, 400)


def serve(port: int) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"CodeFactory dashboard: http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
