"""Read-only views for the terminal: status, one job, subtask waves, and the health check."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import models, state
from .model import ACTIVE, COLUMNS, PRIORITIES
from .util import waves


def status(cfg: dict) -> str:
    rows = list(state.load_index()["jobs"].values())
    active = sum(1 for r in rows if r["state"] in ACTIVE)
    out = [f"capacity: {active}/{cfg['max_parallel_jobs']} active jobs", ""]
    for col in COLUMNS:
        cards = sorted((r for r in rows if r["state"] == col), key=lambda r: (PRIORITIES.index(r["priority"]), r["queue_pos"]))
        if not cards and col in {"REJECTED", "FAILED"}:
            continue
        out.append(f"{col} ({len(cards)})")
        out += [f"  [{r['priority']}] {r['job_id']}  {r['title']}" for r in cards]
    state.render_board()
    return "\n".join(out)


def models_list(cfg: dict) -> str:
    out = ["Claude models (models.claude in the config):"]
    out += [f"  {m}" for m in cfg["models"]["claude"]]
    out += ["", "Local models (LM Studio, `lms ls`):"]
    try:
        lm = models.LMStudio(cfg)
        loaded = {m.get("modelKey") for m in lm.loaded()} | {m.get("identifier") for m in lm.loaded()}
        out += [f"  local:{k}" + ("   (loaded now)" if k in loaded else "") for k in lm.installed()]
    except FactoryError as e:
        out.append(f"  unavailable: {e}")
    out += ["", "Set them for a run:  factory.py profile set build=local:<key> review=<claude-model> ..."]
    return "\n".join(out)


def profile(cfg: dict) -> str:
    prof = models.load_profile()
    if not prof.get("stages"):
        head = "no run profile: every stage uses the models in the config"
    else:
        head = f"run profile (set {prof.get('updated_at', '?')}):"
    out = [head, ""]
    out += [f"  {stage:<20} {spec}" for stage, spec in models.table(prof, cfg)]
    return "\n".join(out)


NEXT_STEP = {
    "INTAKE": "step (intake)", "SPEC": "step (spec-writer)", "BUILD": "step (builder)",
    "REVIEW": "step (reviewers)", "SPLIT_REVIEW": "split approve | split cancel",
    "NEEDS_HUMAN": "feedback | reject | set", "NEEDS_INFO": "answer on the issue; sync picks it up",
    "MERGE_PENDING": "merge", "WAITING_CHILDREN": "wait for sub-issues",
}


def show(job_id: str) -> str:
    st = state.load_state(job_id)
    shown = {k: v for k, v in st.items() if k != "history"}
    out = [json.dumps(shown, indent=2), "", "history (last 5):"]
    out += [f"  {h['at']}  {h['from']} -> {h['to']}  {h['note']}" for h in st["history"][-5:]]
    if st["state"] in NEXT_STEP:
        out.append(f"\nnext: {NEXT_STEP[st['state']]}")
    return "\n".join(out)


def plan(job_id: str, cfg: dict) -> str:
    from .decide import validate_plan
    p = validate_plan(job_id, cfg)
    return "\n".join(f"wave {n}: " + ", ".join(f"{s['id']} ({s['specialist']})" for s in w)
                     for n, w in enumerate(waves(p["subtasks"]), 1))


def doctor(cfg: dict) -> str:
    from . import paths
    out, ok = [], True

    def check(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        out.append(f"[{'ok' if good else 'FAIL'}] {label}{' — ' + detail if detail else ''}")

    check("config loads", True, str(paths.CONFIG))
    for key, st in cfg["stages"].items():
        check(f"agent for stage {key}", (paths.AGENTS / f"{st['agent']}.md").exists(), st["agent"])
    for name, rv in cfg["reviewers"]["catalog"].items():
        check(f"reviewer {name}", (paths.AGENTS / f"{rv['agent']}.md").exists(), rv["agent"])
    for name in cfg["agents"]["catalog"]:
        check(f"specialist {name}", (paths.AGENTS / f"{name}.md").exists())
    check("gh on PATH", shutil.which("gh") is not None)
    chosen = [s for s in models.load_profile().get("stages", {}).values() if models.is_local(s)]
    if chosen:
        check("lms on PATH (run profile uses local models)", shutil.which(cfg["models"]["local"]["command"]) is not None)
        lm = models.LMStudio(cfg)
        check("LM Studio server answers", lm.healthy(), lm.base)
    check("runner on PATH", shutil.which(cfg["runner"]["command"]) is not None, cfg["runner"]["command"])
    check("target_repo set", bool(cfg.get("target_repo")), cfg.get("target_repo") or "empty")
    if cfg.get("target_repo"):
        checks = Path(cfg["target_repo"]) / cfg["checks_command"]
        check("checks script present", checks.exists(), str(checks))
        check("checks script is not the placeholder", checks.exists() and "PLACEHOLDER" not in checks.read_text(),
              "copy a template from CodeFactory/templates/checks/ and fill it in")
    out.append("\nall checks passed" if ok else "\nsome checks failed")
    return "\n".join(out)
