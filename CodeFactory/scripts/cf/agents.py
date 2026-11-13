"""Agent prompts and runners: how a stage's agent is called, and where its output must go."""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import paths, state, util
from .util import FactoryError


def load_agent(name: str) -> str:
    """The agent's markdown body, without its frontmatter."""
    p = paths.AGENTS / f"{name}.md"
    if not p.exists():
        raise FactoryError(f"no agent definition: {p}")
    text = p.read_text()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


def build_prompt(st: dict, cfg: dict, agent: str, output_rel: str, extra: str = "") -> str:
    jd = state.job_dir(st["job_id"])
    files = [f for f in ["issue.md", "intake.md", "spec.md", "build.md", "build/plan.json", "build/split.json"]
             if (jd / f).exists()]
    files += [p.name for p in sorted(jd.glob("intake-*.md"))]
    if st.get("round"):
        for n in sorted({st["round"], st["round"] - 1}):
            d = jd / f"round-{n}"
            if n >= 1 and d.exists():
                files += [str(p.relative_to(jd)) for p in sorted(d.glob("*.md"))]
    lines = [
        load_agent(agent).rstrip(), "", "---", "# Job context (injected by the controller)",
        f"- Job folder: {jd}/",
        f"- Branch: {st['branch']}",
        f"- Worktree: {st.get('worktree') or '(none)'}",
        f"- Base: origin/{cfg['github']['base_branch']}",
        f"- Issue: #{st['issue']}",
        f"- Round: {st['round']} of {st['round_limit']}",
        f"- Read first: {', '.join(files) or '(nothing yet)'}",
        f"- Write your output to: {jd / output_rel}",
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines) + "\n"


def run_agent(prompt: str, model: str, cwd, log: Path, cfg: dict, dry: bool) -> str:
    cmd = [cfg["runner"]["command"], "-p", "--model", model, *cfg["runner"].get("extra_args", [])]
    if dry:
        return f"DRY RUN: {' '.join(cmd)} (prompt {len(prompt)} chars, cwd {cwd or '.'})"
    if not shutil.which(cmd[0]):
        raise FactoryError(f"runner not found on PATH: {cmd[0]}")
    res = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"$ {' '.join(cmd)}\nexit={res.returncode}\n--- stdout ---\n{res.stdout}\n"
                   f"--- stderr ---\n{res.stderr}\n")
    if res.returncode != 0:
        raise FactoryError(f"agent exited {res.returncode}, see {log}")
    return f"ok, log {log.name}"


def execute(job_id: str, items: list[dict], cfg: dict, dry: bool, out=print) -> None:
    """Run agent items in parallel, up to max_parallel_subtasks at a time."""
    st = state.load_state(job_id)
    cwd = st.get("worktree") or cfg.get("target_repo") or None

    def one(it: dict):
        prompt = build_prompt(st, cfg, it["agent"], it["output"], it.get("extra", ""))
        log = state.job_dir(job_id) / "logs" / f"{it['tag']}-{util.stamp()}.log"
        return it["tag"], run_agent(prompt, it["model"], cwd, log, cfg, dry)

    with cf.ThreadPoolExecutor(max_workers=max(1, cfg["max_parallel_subtasks"])) as ex:
        for fut in cf.as_completed([ex.submit(one, it) for it in items]):
            tag, res = fut.result()
            out(f"  {tag}: {res}")


def stage_items(key: str, cfg: dict, st: dict, phase: str | None = None) -> list[dict]:
    """Agent items for one stage key: intake | spec | build | review.<name>."""
    if key.startswith("review."):
        name = key.split(".", 1)[1]
        rv = cfg["reviewers"]["catalog"][name]
        return [{"agent": rv["agent"], "model": rv["model"], "tag": f"review-{name}",
                 "output": f"round-{st['round']}/review-{name}.md",
                 "extra": f"Review round {st['round']}. Read round-{st['round']}/checks.md first."}]
    stage = cfg["stages"][key]
    if key == "build":
        notes = {
            "plan": ("Phase: plan. Decide the mode (single | subtasks | split) and pick specialists only from "
                     "the catalog in your prompt. Write build/plan.json. For mode split, also write build/split.json "
                     "and set build.md to STATUS: SPLIT."),
            "integrate": ("Phase: integrate. Read build/subtasks/*.md, finish the work, commit, push, make sure the "
                          "draft PR exists, and write build.md with STATUS: READY or BLOCKED."),
            "revise": (f"Phase: revise. Read round-{st['round'] - 1}/checks.md and any review-*.md there and fix "
                       "only what they ask. Commit, push, update build.md with STATUS: READY or BLOCKED."),
        }
        output = "build/plan.json" if phase == "plan" else "build.md"
        return [{"agent": stage["agent"], "model": stage["model"], "tag": f"build-{phase}",
                 "output": output, "extra": notes[phase]}]
    return [{"agent": stage["agent"], "model": stage["model"], "tag": key,
             "output": {"intake": "intake.md", "spec": "spec.md"}[key], "extra": ""}]


def subtask_items(plan: dict, wave: list[dict], cfg: dict) -> list[dict]:
    return [{
        "agent": s["specialist"], "model": cfg["agents"]["catalog"][s["specialist"]]["model"],
        "tag": f"sub-{s['id']}", "output": f"build/subtasks/{s['id']}.md",
        "extra": "Your subtask (from build/plan.json):\n```json\n" + json.dumps(s, indent=2) + "\n```",
    } for s in wave]
