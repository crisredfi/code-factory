"""Agent prompts and runners: how a stage's agent is called, and where its output must go."""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import shutil
from pathlib import Path

from . import models, paths, state, util, watchdog
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
    files += [p.name for p in sorted(jd.glob("feedback-*.md"))]  # human feedback sent from NEEDS_HUMAN
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


def run_agent(prompt: str, model: str, cwd, log: Path, cfg: dict, dry: bool,
              env: dict | None = None, watch: dict | None = None, job_dir: Path | None = None) -> str:
    """Run one `claude -p` agent. Raises watchdog.Stuck if the watchdog killed it for making no progress.

    The agent's working directory is the target repo or the job's worktree. The job folder lives outside it,
    so it is added explicitly with --add-dir. Without that the runner refuses to read or write the job's files.
    """
    cmd = [cfg["runner"]["command"], "-p", "--model", model, *cfg["runner"].get("extra_args", [])]
    if job_dir is not None:
        cmd += ["--add-dir", str(job_dir)]
    if dry:
        return f"DRY RUN: {' '.join(cmd)} (prompt {len(prompt)} chars, cwd {cwd or '.'})"
    if not shutil.which(cmd[0]):
        raise FactoryError(f"runner not found on PATH: {cmd[0]}")
    code, _out = watchdog.run_watched(cmd, prompt, cwd, env or dict(os.environ), log, cfg["self_heal"],
                                      **(watch or {}))
    if code != 0:
        raise FactoryError(f"agent exited {code}, see {log}")
    return f"ok, log {log.name}"


def _run_stage_once(job_id: str, it: dict, spec: str, prompt: str, cwd, log: Path, cfg: dict, dry: bool,
                    out) -> str:
    """One attempt. A local model is loaded (and the LM Studio lock held) before its agent runs."""
    watch = {"worktree": cwd and str(cwd), "job_dir": state.job_dir(job_id)}
    if dry or not models.is_local(spec):
        # Claude runs never get the local window settings, even if the shell exports them.
        cloud_env = dict(os.environ)
        cloud_env.pop(models.COMPACT_ENV, None)
        cloud_env.pop(models.WINDOW_ENV, None)
        return run_agent(prompt, spec, cwd, log, cfg, dry, env=cloud_env, watch=watch, job_dir=watch["job_dir"])
    with models.local_turn(cfg, spec, log=lambda msg: out(f"  {it['tag']}: {msg}")) as lm:
        watch |= {"local": True, "local_up": lm.healthy}
        return run_agent(prompt, models.local_key(spec), cwd, log, cfg, dry,
                         env=lm.child_env(dict(os.environ)), watch=watch, job_dir=watch["job_dir"])


def _note_heal(job_id: str, text: str) -> None:
    """Keep every stuck attempt in the job's log folder, so a human can read what happened."""
    d = state.job_dir(job_id) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "self-heal.log", "a") as fh:
        fh.write(f"{util.now()} {text}\n")


def execute(job_id: str, items: list[dict], cfg: dict, dry: bool, out=print) -> None:
    """Run agent items in parallel, up to max_parallel_subtasks at a time.

    Each item's model comes from the run profile (models.py), or from the config when the profile does not
    name its stage. A run the watchdog kills is retried, on the fallback model when one is set, up to
    self_heal.max_retries times. After that it raises StuckAgent.
    """
    st = state.load_state(job_id)
    cwd = st.get("worktree") or cfg.get("target_repo") or None
    profile = models.load_profile()
    heal = cfg["self_heal"]
    attempts = 1 + (heal["max_retries"] if heal["enabled"] else 0)

    def one(it: dict):
        stage = it.get("stage", it["tag"])
        for attempt in range(1, attempts + 1):
            spec = it["model"] if it.get("pinned") else models.resolve(profile, stage, it["model"])
            if attempt > 1 and heal.get("fallback_model"):
                spec = heal["fallback_model"]
            prompt = build_prompt(st, cfg, it["agent"], it["output"], it.get("extra", ""))
            log = state.job_dir(job_id) / "logs" / f"{it['tag']}-{util.stamp()}.log"
            try:
                return it["tag"], _run_stage_once(job_id, it, spec, prompt, cwd, log, cfg, dry, out)
            except watchdog.Stuck as e:
                _note_heal(job_id, f"{it['tag']} attempt {attempt} on {spec}: {e}")
                out(f"  {it['tag']}: stuck on attempt {attempt} ({spec}): {e}")
                if attempt == attempts:
                    raise watchdog.StuckAgent(f"{it['tag']} stuck after {attempt} attempt(s) on {spec}: {e}")
        raise AssertionError("unreachable")

    with cf.ThreadPoolExecutor(max_workers=max(1, cfg["max_parallel_subtasks"])) as ex:
        for fut in cf.as_completed([ex.submit(one, it) for it in items]):
            tag, res = fut.result()
            out(f"  {tag}: {res}")


def catalog_block(cfg: dict) -> str:
    """The specialists the build lead may deploy, with the signals that route work to each one.
    Injected into the plan prompt so the model never has to guess a name."""
    lines = ["Specialist catalog (the only names you may use in plan.json):"]
    for name, spec in cfg["agents"]["catalog"].items():
        lines.append(f"- {name}: {spec['description']}")
        if spec.get("use_when"):
            lines.append(f"  use when: {spec['use_when']}")
    return "\n".join(lines)


def stage_items(key: str, cfg: dict, st: dict, phase: str | None = None) -> list[dict]:
    """Agent items for one stage key: intake | spec | build | review.<name>."""
    if key.startswith("review."):
        name = key.split(".", 1)[1]
        rv = cfg["reviewers"]["catalog"][name]
        return [{"agent": rv["agent"], "model": rv["model"], "tag": f"review-{name}", "stage": key,
                 "output": f"round-{st['round']}/review-{name}.md",
                 "extra": f"Review round {st['round']}. Read round-{st['round']}/checks.md first."}]
    stage = cfg["stages"][key]
    if key == "build":
        notes = {
            "plan": ("Phase: plan. Detect the layer of each acceptance criterion, decide the mode "
                     "(single | subtasks | split), and pick specialists only from the catalog below. Write "
                     "build/plan.json. For mode split, also write build/split.json and set build.md to "
                     "STATUS: SPLIT.\n\n" + catalog_block(cfg)),
            "integrate": ("Phase: integrate. Read build/subtasks/*.md, finish the work, commit, push, make sure the "
                          "draft PR exists, and write build.md with STATUS: READY or BLOCKED."),
            "revise": (f"Phase: revise. Read round-{st['round'] - 1}/checks.md and any review-*.md there and fix "
                       "only what they ask. Commit, push, update build.md with STATUS: READY or BLOCKED."),
        }
        output = "build/plan.json" if phase == "plan" else "build.md"
        return [{"agent": stage["agent"], "model": stage["model"], "tag": f"build-{phase}", "stage": "build",
                 "output": output, "extra": notes[phase]}]
    return [{"agent": stage["agent"], "model": stage["model"], "tag": key, "stage": key,
             "output": {"intake": "intake.md", "spec": "spec.md"}[key], "extra": ""}]


def subtask_items(plan: dict, wave: list[dict], cfg: dict) -> list[dict]:
    return [{
        "agent": s["specialist"], "model": cfg["agents"]["catalog"][s["specialist"]]["model"],
        "tag": f"sub-{s['id']}", "stage": "specialists", "output": f"build/subtasks/{s['id']}.md",
        "extra": "Your subtask (from build/plan.json):\n```json\n" + json.dumps(s, indent=2) + "\n```",
    } for s in wave]
