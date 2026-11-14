"""Run a job's stage: call its agents, then apply the gate. This is what `step` and `tick` use."""
from __future__ import annotations

from . import agents, decide, lifecycle, reviewers, state, watchdog
from .decide import validate_plan
from . import util
from .util import FactoryError, waves


def apply(job_id: str, cfg: dict) -> tuple[str, str]:
    """Check the gate for the job's current stage, move the job, return (new state, note)."""
    st = state.load_state(job_id)
    decider = decide.DECIDERS.get(st["state"])
    if decider is None:
        raise FactoryError(f"nothing to advance from {st['state']}")
    try:
        to, note = decider(st, cfg)
    except FactoryError:
        # The output this stage produced is not valid: its agents run again on the next attempt.
        _forget_stage(job_id)
        raise
    if to == "MERGE_PENDING":
        # Gate and every reviewer passed: the flow pushes and opens the PR. A failure here leaves the job
        # where it was, so the next tick retries it.
        url = lifecycle.publish_pr(st, cfg)
        note = f"{note}; PR {url}"
        if not cfg["auto_merge"]["enabled"]:
            state.set_state(st, to, note)
            return to, note
        state.set_state(st, to, note)
        try:
            lifecycle.merge_job(state.load_state(job_id), cfg, dry=False)
        except FactoryError as e:
            state.set_state(state.load_state(job_id), "NEEDS_HUMAN", f"auto-merge failed: {e}")
            return "NEEDS_HUMAN", f"auto-merge failed: {e}"
        return "COMPLETE", f"auto-merged {url}"
    state.set_state(st, to, note)
    return to, note


def _forget_stage(job_id: str) -> None:
    st = state.load_state(job_id)
    prefix = f"{st['state']}|{st.get('phase')}|{st.get('round')}|"
    kept = [k for k in st.get("done", []) if not k.startswith(prefix)]
    if len(kept) != len(st.get("done", [])):
        st["done"] = kept
        state.save_state(st)


def step_build(job_id: str, cfg: dict, dry: bool, out) -> None:
    st = state.load_state(job_id)
    if st.get("phase") in (None, "plan"):
        agents.execute(job_id, agents.stage_items("build", cfg, st, "plan"), cfg, dry, out)
        if dry:
            return
        to, note = apply(job_id, cfg)
        out(f"{job_id}: BUILD -> {to} ({note})")
        if to != "BUILD":
            return
        st = state.load_state(job_id)
    if st.get("phase") == "subtasks":
        plan = validate_plan(job_id, cfg)
        for n, wave in enumerate(waves(plan["subtasks"]), 1):
            out(f"  wave {n}: {', '.join(s['id'] for s in wave)}")
            agents.execute(job_id, agents.subtask_items(plan, wave, cfg), cfg, dry, out)
        if dry:
            return
        st = state.load_state(job_id)  # the subtask runs recorded their markers on disk; keep them
        st["phase"] = "integrate"
        state.save_state(st)
    st = state.load_state(job_id)
    if st.get("phase") in {"integrate", "revise"}:
        agents.execute(job_id, agents.stage_items("build", cfg, st, st["phase"]), cfg, dry, out)
        if dry:
            return
        to, note = apply(job_id, cfg)
        out(f"{job_id}: BUILD -> {to} ({note})")


def step_job(job_id: str, cfg: dict, dry: bool = False, out=print) -> None:
    """Run one job's current stage. If an agent stayed stuck through every retry, the job waits for a human.

    Any other failure is recorded on the job (last_error, history) and retried on the next tick. After
    self_heal.max_step_errors failures in a row, the job waits for a human with the last error as the reason.
    """
    with state.job_lock(job_id):
        try:
            _run_stage(job_id, cfg, dry, out)
        except watchdog.StuckAgent as e:
            st = state.load_state(job_id)
            state.set_state(st, "NEEDS_HUMAN", f"self-heal gave up: {e}")
            out(f"{job_id}: NEEDS_HUMAN (self-heal gave up: {e})")
            return
        except FactoryError as e:
            if dry:
                raise
            _record_error(job_id, cfg, str(e), out)
            return
        if not dry:
            _clear_error(job_id)


def _record_error(job_id: str, cfg: dict, message: str, out) -> None:
    st = state.load_state(job_id)
    st["error_streak"] = st.get("error_streak", 0) + 1
    st["last_error"] = {"at": util.now(), "message": message}
    limit = cfg.get("self_heal", {}).get("max_step_errors", 3)
    if st["error_streak"] >= limit:
        note = f"gave up after {st['error_streak']} errors: {message}"
        st["error_streak"] = 0
        out(f"{job_id}: NEEDS_HUMAN ({note})")
        state.set_state(st, "NEEDS_HUMAN", note)
        return
    out(f"{job_id}: error {st['error_streak']} of {limit}, retrying on the next tick: {message}")
    if not (st.get("history") and st["history"][-1]["note"].endswith(message)):
        st["history"].append({"at": util.now(), "from": st["state"], "to": st["state"],
                              "note": f"error {st['error_streak']} of {limit}, retry on next tick: {message}"})
    state.save_state(st)


def _clear_error(job_id: str) -> None:
    st = state.load_state(job_id)
    if st.get("error_streak") or st.get("last_error"):
        st["error_streak"] = 0
        st["last_error"] = None
        state.save_state(st)


def _run_stage(job_id: str, cfg: dict, dry: bool, out) -> None:
    st = state.load_state(job_id)
    s = st["state"]
    if s == "BUILD":
        step_build(job_id, cfg, dry, out)
        return
    if s in {"INTAKE", "SPEC"}:
        agents.execute(job_id, agents.stage_items(s.lower(), cfg, st), cfg, dry, out)
    elif s == "REVIEW":
        items = [it for n in reviewers.review_names(st, cfg) for it in agents.stage_items(f"review.{n}", cfg, st)]
        agents.execute(job_id, items, cfg, dry, out)
    else:
        out(f"{job_id}: nothing to run in {s}")
        return
    if dry:
        return
    to, note = apply(job_id, cfg)
    out(f"{job_id}: {s} -> {to} ({note})")
