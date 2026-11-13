"""Worktrees and the deterministic gate. The gate decides, not an agent."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import state, util
from .util import FactoryError


def target(cfg: dict) -> Path:
    if not cfg.get("target_repo"):
        raise FactoryError("set target_repo in factory.config.json first")
    return Path(cfg["target_repo"])


def worktree_path(st: dict, cfg: dict) -> Path:
    return target(cfg) / cfg["worktree_dir"] / st["job_id"]


def ensure_worktree(st: dict, cfg: dict) -> Path:
    """Create (or reuse) the job's checkout on its own branch, from the base branch."""
    wt = worktree_path(st, cfg)
    if wt.exists():
        st["worktree"] = str(wt)
        return wt
    base = cfg["github"]["base_branch"]
    util.git(target(cfg), "fetch", "origin", base)
    wt.parent.mkdir(parents=True, exist_ok=True)
    util.git(target(cfg), "worktree", "add", "-b", st["branch"], str(wt), f"origin/{base}")
    st["worktree"] = str(wt)
    return wt


def run_gate(st: dict, cfg: dict) -> str:
    """Branch hygiene, protected paths, then the base checkout's checks script. Writes round-N/checks.md."""
    base = cfg["github"]["base_branch"]
    wt = Path(st["worktree"]) if st.get("worktree") else None
    if wt is None or not wt.exists():
        raise FactoryError("no worktree for this job")
    rd = state.round_dir(st)
    rd.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    git = util.git
    head = git(wt, "rev-parse", "HEAD").strip()
    if git(wt, "status", "--porcelain").strip():
        problems.append("the worktree has uncommitted changes")
    ahead = int(git(wt, "rev-list", "--count", f"origin/{base}..HEAD").strip() or 0)
    if ahead == 0:
        problems.append(f"{st['branch']} has no commits on top of origin/{base}")
    changed = git(wt, "diff", "--name-only", f"origin/{base}...HEAD").split()
    touched = [f for f in changed
               if any(f == p or f.startswith(p.rstrip("/") + "/") for p in cfg["protected_paths"])]
    if touched:
        problems.append("touches protected paths: " + ", ".join(touched))
    checks = target(cfg) / cfg["checks_command"]
    output = ""
    if not checks.exists():
        problems.append(f"checks script missing: {checks}")
    elif not problems:
        try:
            res = subprocess.run(["bash", str(checks)], cwd=str(wt), capture_output=True, text=True,
                                 timeout=cfg.get("checks_timeout_s", 1800))
            output = res.stdout + res.stderr
            if res.returncode != 0:
                problems.append(f"checks exited {res.returncode}")
        except subprocess.TimeoutExpired:
            problems.append("checks timed out")
    verdict = "FAIL" if problems else "PASS"
    (rd / "checks.log").write_text(output)
    tail = "\n".join(output.splitlines()[-60:])
    summary = "".join(f"- {p}\n" for p in problems) or "- checks passed\n"
    (rd / "checks.md").write_text(f"GATE: {verdict}\nCOMMIT: {head}\n\n## Summary\n{summary}\n"
                                  f"## Output (tail)\n```\n{tail}\n```\n")
    return verdict
