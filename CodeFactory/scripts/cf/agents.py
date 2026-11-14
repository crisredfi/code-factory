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


def stage_key(st: dict, tag: str) -> str:
    """Identifies one agent run in one stage of one round. Recorded on the job when that run succeeds."""
    return f"{st['state']}|{st.get('phase')}|{st.get('round')}|{tag}"


def _mark_done(job_id: str, key: str) -> None:
    st = state.load_state(job_id)
    done = st.setdefault("done", [])
    if key not in done:
        done.append(key)
        state.save_state(st)


def execute(job_id: str, items: list[dict], cfg: dict, dry: bool, out=print) -> None:
    """Run agent items in parallel, up to max_parallel_subtasks at a time.

    An item that already succeeded in this stage and round is not run again, so a retry after a failure
    only runs what failed. Every other item runs even when one of them fails; the failures are raised
    together after the rest have finished and been recorded.

    Each item's model comes from the run profile (models.py), or from the config when the profile does not
    name its stage. A run the watchdog kills is retried, on the fallback model when one is set, up to
    self_heal.max_retries times. After that it raises StuckAgent.
    """
    st = state.load_state(job_id)
    cwd = st.get("worktree") or cfg.get("target_repo") or None
    profile = models.load_profile()
    heal = cfg["self_heal"]
    attempts = 1 + (heal["max_retries"] if heal["enabled"] else 0)
    done = set(st.get("done", []))

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

    todo = []
    for it in items:
        if not dry and stage_key(st, it["tag"]) in done:
            out(f"  {it['tag']}: already done in this round, not run again")
            continue
        todo.append(it)

    failures, stuck = [], None
    with cf.ThreadPoolExecutor(max_workers=max(1, cfg["max_parallel_subtasks"])) as ex:
        futs = {ex.submit(one, it): it for it in todo}
        for fut in cf.as_completed(futs):
            it = futs[fut]
            try:
                tag, res = fut.result()
            except watchdog.StuckAgent as e:
                stuck = stuck or e
                continue
            except FactoryError as e:
                failures.append(f"{it['tag']}: {e}")
                continue
            out(f"  {tag}: {res}")
            if not dry:
                _mark_done(job_id, stage_key(st, it["tag"]))
    if stuck is not None:
        raise stuck
    if failures:
        raise FactoryError("; ".join(failures))


def catalog_block(cfg: dict) -> str:
    """The specialists the build lead may deploy, with the signals that route work to each one.
    Injected into the plan prompt so the model never has to guess a name."""
    lines = ["Specialist catalog (the only names you may use in plan.json):"]
    for name, spec in cfg["agents"]["catalog"].items():
        lines.append(f"- {name}: {spec['description']}")
        if spec.get("use_when"):
            lines.append(f"  use when: {spec['use_when']}")
    return "\n".join(lines)


# Given to the builder in every phase that commits. The local model follows this literally.
LOCAL_FIX = (
    "\n\nLOCAL TOOL STATE: the folders `.claude/`, `.build/`, `DerivedData/`, `.swiftpm/` and the file `.DS_Store` "
    "must NOT be in any commit. You do not remove them: the factory does that before the gate. Never run `git rm`, "
    "`git reset` or `git commit --amend`, and never rewrite history."
)


def findings_round(st: dict) -> int | None:
    """The latest earlier round that has findings (checks or reviews). Feedback and blocked rounds may have skipped
    numbers, so 'the previous round' is not always there; this one is."""
    if "job_id" not in st or not st.get("round"):
        return None
    jd = state.job_dir(st["job_id"])
    for n in range(st["round"] - 1, 0, -1):
        d = jd / f"round-{n}"
        if d.exists() and (any(d.glob("checks.md")) or any(d.glob("review-*.md"))):
            return n
    return None


def _findings_text(st: dict) -> str:
    n = findings_round(st)
    if n is None:
        return "There are no findings files; act on the feedback-*.md files named in your prompt."
    return f"The latest findings are in round-{n}/ (checks.md and review-*.md)."


def coding_model(st: dict, cfg: dict, fallback: str) -> str:
    """The model that writes code for this job: the hard one when intake rated the job HARD, else the default.
    Without a `coding` section in the config, the fallback (the specialist's own model) is used."""
    coding = cfg.get("coding")
    if not coding:
        return fallback
    return coding["hard"] if st.get("difficulty") == "HARD" else coding["default"]


def stage_items(key: str, cfg: dict, st: dict, phase: str | None = None) -> list[dict]:
    """Agent items for one stage key: intake | spec | build | review.<name>.

    The build stage is the orchestrator (factory-builder, on the build model). It plans, checks and commits.
    The code itself is written by coders (specialists in subtasks, factory-coder in revise), on the coding model.
    """
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
                     "STATUS: SPLIT. You plan and check the work; you do not write product code.\n\n"
                     + catalog_block(cfg)),
            "integrate": ("Phase: integrate. Read build/subtasks/*.md and check the work against the acceptance "
                          "criteria. Run the project's tests from the worktree. Do NOT write product code yourself: "
                          "the coders do that. If the work is complete and the tests pass, commit. If it is not, "
                          "write STATUS: BLOCKED with the reason. Do NOT push and do NOT open a PR: the factory does "
                          "both after the gate and reviewers pass. Then write build.md with STATUS: READY or BLOCKED."
                          + LOCAL_FIX),
            "revise": (f"Phase: revise-commit. The coder changed the code for this round (see build/revise-code.md). "
                       f"Check those changes against the findings: {_findings_text(st)} Run "
                       "the tests. Do NOT write product code yourself. If the fix is complete, commit. Do NOT push and "
                       "do NOT open a PR. Then write build.md with STATUS: READY or BLOCKED." + LOCAL_FIX),
        }
        output = "build/plan.json" if phase == "plan" else "build.md"
        return [{"agent": stage["agent"], "model": stage["model"], "tag": f"build-{phase}", "stage": "build",
                 "output": output, "extra": notes[phase]}]
    return [{"agent": stage["agent"], "model": stage["model"], "tag": key, "stage": key,
             "output": {"intake": "intake.md", "spec": "spec.md"}[key], "extra": ""}]


def coder_items(st: dict, cfg: dict) -> list[dict]:
    """The revise coder: writes the code that the round's checks and reviews ask for. Runs on the coding model."""
    fallback = cfg["stages"]["build"]["model"]
    return [{"agent": "factory-coder", "model": coding_model(st, cfg, fallback), "tag": "revise-code",
             "stage": "specialists", "output": "build/revise-code.md",
             "extra": (f"Phase: revise. {_findings_text(st)} Read the feedback-*.md files too: a human's instructions win. "
                       "Fix what the findings ask and nothing else. Edit the code in the worktree. Do NOT commit, push or open a PR: the "
                       "orchestrator does that. Then write build/revise-code.md: one line per file you changed, saying "
                       "what changed, and the test command you ran.")}]


def subtask_items(plan: dict, wave: list[dict], cfg: dict, st: dict) -> list[dict]:
    return [{
        "agent": s["specialist"],
        "model": coding_model(st, cfg, cfg["agents"]["catalog"][s["specialist"]]["model"]),
        "tag": f"sub-{s['id']}", "stage": "specialists", "output": f"build/subtasks/{s['id']}.md",
        "extra": "Your subtask (from build/plan.json):\n```json\n" + json.dumps(s, indent=2) + "\n```",
    } for s in wave]
