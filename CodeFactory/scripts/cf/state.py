"""Job state: per-job state.json, the queue index, transitions, the text board and the per-job lock.

Every write to a job goes through save_state(), so state.json and the index never disagree.
"""
from __future__ import annotations

import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path

from . import paths, util
from .model import COLUMNS, PRIORITIES, TRANSITIONS
from .util import FactoryError, now

LOCK = threading.RLock()


def job_dir(job_id: str) -> Path:
    return paths.JOBS / job_id


def load_index() -> dict:
    return util.read_json(paths.INDEX, {"jobs": {}, "next_pos": 1})


def write_index(idx: dict) -> None:
    util.write_json(paths.INDEX, idx)


def load_state(job_id: str) -> dict:
    st = util.read_json(job_dir(job_id) / "state.json", None)
    if st is None:
        raise FactoryError(f"unknown job: {job_id}")
    return st


def index_row(st: dict) -> dict:
    return {
        "job_id": st["job_id"], "issue": st["issue"], "title": st["title"], "url": st.get("url", ""),
        "priority": st["priority"], "queue_pos": st["queue_pos"], "state": st["state"],
        "parent": st.get("parent"), "risk": st.get("risk"), "round": st.get("round", 0),
        "added_at": st["created_at"], "updated_at": st["updated_at"],
    }


def save_state(st: dict) -> None:
    """The single write point for a job: its state.json and its row in the index."""
    with LOCK:
        util.write_json(job_dir(st["job_id"]) / "state.json", st)
        idx = load_index()
        idx["jobs"][st["job_id"]] = index_row(st)
        write_index(idx)


def set_state(st: dict, to: str, note: str = "", force: bool = False) -> None:
    frm = st["state"]
    if to not in TRANSITIONS and to != frm:
        raise FactoryError(f"unknown state: {to}")
    if to != frm and not force and to not in TRANSITIONS.get(frm, set()):
        raise FactoryError(f"{st['job_id']}: illegal transition {frm} -> {to} (use --force to override)")
    st["history"].append({"at": now(), "from": frm, "to": to, "note": note})
    st["state"] = to
    st["updated_at"] = now()
    save_state(st)
    render_board()
    from . import notify  # lazy: notify depends on state
    notify.transitioned(st, frm, to, note)


def new_state(job_id, issue, title, url, labels, cfg, queue_pos) -> dict:
    prio = "P2"
    for p, lbl in cfg["github"]["priority_labels"].items():
        if lbl in labels:
            prio = p
    ts = now()
    return {
        "job_id": job_id, "issue": issue, "title": title, "url": url, "labels": labels,
        "priority": prio, "queue_pos": queue_pos, "state": "QUEUED", "parent": None,
        "blocked_by": [], "children": [], "scope": None, "risk": None,
        "intake_add": [], "intake_drop": [], "reviewers": [], "reviewers_override": None, "notified": [],
        "phase": None, "round": 0, "round_limit": cfg["max_rounds"], "gate": None,
        "branch": f"factory/{job_id}", "worktree": None, "pr": None, "pr_url": None,
        "created_at": ts, "updated_at": ts,
        "history": [{"at": ts, "from": None, "to": "QUEUED", "note": "synced from GitHub"}],
    }


def all_job_ids() -> list[str]:
    if not paths.JOBS.exists():
        return []
    return sorted(p.name for p in paths.JOBS.iterdir() if (p / "state.json").exists())


def all_states() -> list[dict]:
    return [load_state(j) for j in all_job_ids()]


def read_doc(job_id: str, rel: str) -> str:
    p = job_dir(job_id) / rel
    return p.read_text() if p.exists() else ""


def round_dir(st: dict) -> Path:
    return job_dir(st["job_id"]) / f"round-{st['round']}"


def render_board() -> None:
    jobs = list(load_index()["jobs"].values())
    lines = ["# CodeFactory board", "", f"_Generated {now()} by `scripts/factory.py`. Do not edit by hand._", ""]
    for col in COLUMNS:
        rows = sorted((j for j in jobs if j["state"] == col),
                      key=lambda j: (PRIORITIES.index(j["priority"]), j["queue_pos"]))
        lines.append(f"## {col} ({len(rows)})")
        lines += [f"- **{j['priority']}** `{j['job_id']}` {j['title']}" for j in rows] or ["_empty_"]
        lines.append("")
    paths.BOARD.write_text("\n".join(lines))


@contextmanager
def job_lock(job_id: str):
    """Non-blocking per-job lock: one step at a time per job, across processes."""
    path = job_dir(job_id) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise FactoryError(f"{job_id} is already being stepped by another process")
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
