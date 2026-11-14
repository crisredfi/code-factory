"""Watch one agent run and decide whether it is really stuck.

An agent counts as making progress when any of these moves: the CPU time of its process tree (tests and
builds it runs are work), its git worktree (HEAD or changed files), or its job folder (the files it writes).
If none moves for `stuck_after_s` (`local_stuck_after_s` for a local model), the watchdog marks the run
SUSPECT. It becomes STUCK only if the silence lasts another `confirm_s`, so one quiet poll is not enough.
For a local model the LM Studio server is also checked: if it no longer answers, the run is stuck for sure.
A run that exceeds `max_run_s` is stuck whatever it is doing. A stuck run is killed and its process group
is cleaned up. The caller decides whether to retry.
"""
from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import util
from .util import FactoryError


class Stuck(Exception):
    """Raised inside the agent runner when the watchdog killed the run. The reason says why."""


class StuckAgent(FactoryError):
    """An agent stayed stuck through every retry. The stage cannot finish without a human."""


CPU_EPSILON_S = 0.5
_CPU_TIME = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")


def parse_cputime(text: str) -> float:
    """`ps` TIME values: [[dd-]hh:]mm:ss[.ff]. Returns seconds."""
    m = _CPU_TIME.match(text.strip())
    if not m:
        return 0.0
    days, hours, minutes, seconds = m.groups()
    return float(seconds) + 60 * int(minutes) + 3600 * int(hours or 0) + 86400 * int(days or 0)


def tree_cpu(pid: int) -> float:
    """Total CPU seconds used by a process and all its descendants."""
    res = subprocess.run(["ps", "-axo", "pid=,ppid=,time="], capture_output=True, text=True)
    children: dict[int, list[int]] = {}
    cpu: dict[int, float] = {}
    for line in res.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        p, parent = int(parts[0]), int(parts[1])
        cpu[p] = parse_cputime(parts[2])
        children.setdefault(parent, []).append(p)
    total, stack, seen = 0.0, [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        total += cpu.get(p, 0.0)
        stack.extend(children.get(p, []))
    return total


def fingerprint(worktree: str | None, job_dir: Path) -> str:
    """A hash that changes when the worktree or the job's files change. Logs are left out."""
    h = hashlib.sha256()
    if worktree and Path(worktree).exists():
        h.update(util.git(worktree, "rev-parse", "HEAD", check=False).encode())
        h.update(util.git(worktree, "status", "--porcelain", check=False).encode())
    if job_dir.exists():
        for p in sorted(job_dir.rglob("*")):
            if p.is_file() and "logs" not in p.relative_to(job_dir).parts:
                st = p.stat()
                h.update(f"{p.relative_to(job_dir)}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()


class Watch:
    """Judges one running agent. `check()` is called on every poll and returns ("ACTIVE" | "SUSPECT" | "STUCK", reason)."""

    def __init__(self, pid: int, worktree: str | None, job_dir: Path, heal: dict, local: bool,
                 local_up: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic):
        self.pid, self.worktree, self.job_dir = pid, worktree, job_dir
        self.heal, self.local, self.local_up, self.clock = heal, local, local_up, clock
        self.started = self.last_active = clock()
        self.cpu = tree_cpu(pid)
        self.fp = fingerprint(worktree, job_dir)
        self.suspect_since: float | None = None

    def check(self) -> tuple[str, str]:
        now = self.clock()
        cpu, fp = tree_cpu(self.pid), fingerprint(self.worktree, self.job_dir)
        if cpu - self.cpu > CPU_EPSILON_S or fp != self.fp:
            self.last_active = now
            self.suspect_since = None
        self.cpu, self.fp = cpu, fp

        elapsed, idle = now - self.started, now - self.last_active
        if elapsed >= self.heal["max_run_s"]:
            return "STUCK", f"ran {int(elapsed)}s, over max_run_s={self.heal['max_run_s']}"
        limit = self.heal["local_stuck_after_s" if self.local else "stuck_after_s"]
        if idle < limit:
            return "ACTIVE", f"idle {int(idle)}s"
        if self.local and self.local_up is not None and not self.local_up():
            return "STUCK", f"no progress for {int(idle)}s and the LM Studio server is not answering"
        if self.suspect_since is None:
            self.suspect_since = now
            return "SUSPECT", f"no progress for {int(idle)}s, checking again"
        if now - self.suspect_since < self.heal["confirm_s"]:
            return "SUSPECT", f"no progress for {int(idle)}s, still checking"
        return "STUCK", (f"no progress for {int(idle)}s: no CPU in the agent's processes, "
                         "no change in the worktree or the job folder")


def _kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def run_watched(cmd: list[str], prompt: str, cwd, env: dict, log: Path, heal: dict, *,
                worktree: str | None, job_dir: Path, local: bool = False,
                local_up: Callable[[], bool] | None = None) -> tuple[int, str]:
    """Run an agent process and watch it. Returns (exit code, output) or raises Stuck after killing it."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    result: dict[str, str] = {}

    def talk():
        result["out"], result["err"] = proc.communicate(prompt)

    reader = threading.Thread(target=talk, daemon=True)
    reader.start()
    watch = Watch(proc.pid, worktree, job_dir, heal, local, local_up)
    reason = ""
    while reader.is_alive():
        reader.join(heal["poll_s"])
        if not reader.is_alive():
            break
        verdict, why = watch.check()
        if verdict == "STUCK":
            _kill_group(proc)
            reader.join(10)
            reason = why
            break
    reader.join(10)
    stdout, stderr = result.get("out", ""), result.get("err", "")
    log.parent.mkdir(parents=True, exist_ok=True)
    head = f"$ {' '.join(cmd)}\n" + (f"killed by watchdog: {reason}\n" if reason else f"exit={proc.returncode}\n")
    log.write_text(f"{head}--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
    if reason:
        raise Stuck(reason)
    return proc.returncode, stdout
