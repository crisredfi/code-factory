"""Worktrees and the deterministic gate. The gate decides, not an agent."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import state, util
from .util import FactoryError


def is_local_state(path: str, patterns: list[str]) -> bool:
    """True for files a tool writes into the checkout (agent memory, build output, Finder metadata).
    A pattern ending in "/" matches a folder anywhere in the path; any other pattern matches a file name."""
    name = path.rsplit("/", 1)[-1]
    for p in patterns:
        if p.endswith("/"):
            if f"/{p}" in f"/{path}":
                return True
        elif name == p or path == p:
            return True
    return False


def warnings_in(output: str) -> list[str]:
    """Advisory lines from the checks output (a `WARN:` line, or a `warning:` line in the verify summary).
    They are shown on the card and in checks.md; they never fail the gate."""
    out = []
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("WARN:"):
            out.append(s[len("WARN:"):].strip())
        elif s.startswith("verify:") and "warning:" in s:
            out.append(s.split("warning:", 1)[1].strip())
    return out


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


def drop_local_state(wt: Path, base: str, patterns: list[str]) -> list[str]:
    """Take local tool state out of the job's branch with one new commit. The files stay on disk.

    The factory does this itself, because the local builder models did not do it when told to. It only
    commits when the staged change is exactly the removal of local-state paths, so no other work is swept in.
    Returns the paths it removed (empty when there were none). Raises FactoryError if it cannot do so safely.
    """
    git = util.git
    changed = git(wt, "diff", "--name-only", f"origin/{base}...HEAD").split()
    paths = [f for f in changed if is_local_state(f, patterns)]
    if not paths:
        return []
    if git(wt, "diff", "--cached", "--name-only").strip():
        raise FactoryError("cannot remove local tool state: the worktree already has staged changes")
    git(wt, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *paths)
    staged = git(wt, "diff", "--cached", "--name-only").split()
    if not staged or any(not is_local_state(f, patterns) for f in staged):
        git(wt, "reset", "-q")
        raise FactoryError(f"cannot remove local tool state safely; staged: {', '.join(staged) or 'nothing'}")
    git(wt, "commit", "-q", "-m", "Remove local tool state from the branch")
    return sorted(set(paths))


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
    removed = []
    try:
        removed = drop_local_state(wt, base, cfg.get("local_state_paths", []))
    except FactoryError as e:
        problems.append(str(e))
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
    local_state = [f for f in changed if is_local_state(f, cfg.get("local_state_paths", []))]
    if local_state:
        problems.append("commits local tool state: " + ", ".join(local_state) + ". The factory removes "
                        "these before the gate. Do not run git rm yourself.")
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
    warns = warnings_in(output)
    summary = "".join(f"- {p}\n" for p in problems)
    summary += "".join(f"- warning: {w}\n" for w in warns)
    if removed:
        summary += f"- removed local tool state from the branch (kept on disk): {', '.join(removed)}\n"
    summary = summary or "- checks passed\n"
    (rd / "checks.md").write_text(f"GATE: {verdict}\nCOMMIT: {head}\n\n## Summary\n{summary}\n"
                                  f"## Output (tail)\n```\n{tail}\n```\n")
    return verdict
