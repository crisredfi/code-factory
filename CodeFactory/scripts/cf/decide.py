"""The gates: each stage's output file says what happened, and these functions decide the next state.

A decider mutates the in-memory state and returns (next_state, note). It never saves; steps.apply() does.
Deciders may write files (worktrees, gate results), and they raise FactoryError to refuse a move.
"""
from __future__ import annotations

import re

from . import gate, reviewers, state, util
from .model import MODES, REVIEW_VERDICTS, RISKS
from .util import FactoryError, field, first_word, names_in, section


def decide_intake(st: dict, cfg: dict) -> tuple[str, str]:
    doc = state.read_doc(st["job_id"], "intake.md")
    decision = first_word(field(doc, "DECISION"))
    if decision not in {"ACCEPT", "NEEDS-INFO", "REJECT"}:
        raise FactoryError("intake.md needs 'DECISION: ACCEPT | NEEDS-INFO | REJECT'")
    risk = first_word(field(doc, "RISK"))
    if risk not in RISKS:
        raise FactoryError("intake.md needs 'RISK: LOW | MEDIUM | HIGH'")
    add, drop = names_in(field(doc, "REVIEWERS_ADD")), names_in(field(doc, "REVIEWERS_DROP"))
    catalog = cfg["reviewers"]["catalog"]
    bad = [n for n in add + drop if n not in catalog]
    if bad:
        raise FactoryError(f"intake.md names unknown reviewers: {', '.join(bad)}")
    st.update(risk=risk, scope=field(doc, "SCOPE").lower() or None, intake_add=add, intake_drop=drop)
    st["reviewers"] = reviewers.review_names(st, cfg)
    if decision == "NEEDS-INFO":
        return "NEEDS_INFO", "intake needs more information from the issue author"
    if decision == "REJECT":
        return "REJECTED", "intake rejected"
    if risk == "HIGH":
        return "NEEDS_HUMAN", "accepted but risk HIGH: a human approves before the spec"
    gate.ensure_worktree(st, cfg)
    st["phase"] = "plan"
    return "SPEC", f"accepted, scope={st['scope'] or 'n/a'}, risk={risk}, reviewers={', '.join(st['reviewers'])}"


def decide_spec(st: dict, cfg: dict) -> tuple[str, str]:
    doc = state.read_doc(st["job_id"], "spec.md")
    criteria = re.findall(r"^\s*-\s\[\s\]\s+\S", doc, re.MULTILINE)
    if not criteria:
        raise FactoryError("spec.md needs at least one '- [ ]' acceptance criterion")
    st["round"], st["round_limit"], st["phase"] = 1, cfg["max_rounds"], "plan"
    return "BUILD", f"spec has {len(criteria)} acceptance criteria"


def validate_plan(job_id: str, cfg: dict) -> dict:
    plan = util.read_json(state.job_dir(job_id) / "build" / "plan.json", None)
    if plan is None:
        raise FactoryError("build/plan.json is missing")
    mode = plan.get("mode")
    if mode not in MODES:
        raise FactoryError(f"plan.json mode must be one of {sorted(MODES)}")
    catalog = cfg["agents"]["catalog"]
    subtasks = plan.get("subtasks", [])
    for s in subtasks:
        if s.get("specialist") not in catalog:
            raise FactoryError(f"plan.json picks unknown specialist {s.get('specialist')!r}; "
                               f"catalog: {', '.join(catalog)}")
    if mode in {"single", "subtasks"} and not subtasks:
        raise FactoryError(f"plan.json mode {mode} needs at least one subtask")
    if mode == "single" and len(subtasks) != 1:
        raise FactoryError("plan.json mode single needs exactly one subtask")
    util.waves(subtasks)
    return plan


def validate_split(job_id: str) -> list[dict]:
    split = util.read_json(state.job_dir(job_id) / "build" / "split.json", None)
    issues = (split or {}).get("issues", [])
    if len(issues) < 2:
        raise FactoryError("build/split.json needs at least two issues")
    for i in issues:
        if not i.get("title") or not i.get("body") or not i.get("id"):
            raise FactoryError("every split issue needs id, title and body")
    util.waves(issues)
    return issues


def decide_build(st: dict, cfg: dict) -> tuple[str, str]:
    job = st["job_id"]
    if st.get("phase") in (None, "plan"):
        plan = validate_plan(job, cfg)
        if plan["mode"] == "split":
            issues = validate_split(job)
            return "SPLIT_REVIEW", f"builder proposed {len(issues)} sub-issues: review the drafts"
        st["phase"] = "subtasks"
        return "BUILD", f"plan accepted ({plan['mode']}, {len(plan['subtasks'])} subtasks)"
    if st["phase"] == "subtasks":
        raise FactoryError("subtasks have not run yet; use `step`")
    doc = state.read_doc(job, "build.md")
    status = first_word(field(doc, "STATUS"))
    if status == "BLOCKED":
        return "NEEDS_HUMAN", "builder blocked: " + (section(doc, "Blocked") or "see build.md")[:200]
    if status != "READY":
        raise FactoryError("build.md needs 'STATUS: READY | BLOCKED'")
    st["pr_url"] = field(doc, "DRAFT_PR") or st.get("pr_url")
    verdict = gate.run_gate(st, cfg)
    st["gate"] = verdict
    if verdict == "PASS":
        st["phase"] = None
        return "REVIEW", f"gate PASS in round {st['round']}"
    if st["round"] >= st["round_limit"]:
        return "NEEDS_HUMAN", f"gate failed in round {st['round']}, the last allowed round"
    st["round"] += 1
    st["phase"] = "revise"
    return "BUILD", f"gate FAIL: builder resumed for round {st['round']}"


def decide_review(st: dict, cfg: dict) -> tuple[str, str]:
    verdicts = {}
    for name in reviewers.review_names(st, cfg):
        doc = state.read_doc(st["job_id"], f"round-{st['round']}/review-{name}.md")
        if not doc:
            raise FactoryError(f"missing round-{st['round']}/review-{name}.md")
        v, r = first_word(field(doc, "VERDICT")), first_word(field(doc, "RISK"))
        if v not in REVIEW_VERDICTS or r not in RISKS:
            raise FactoryError(f"review-{name}.md needs 'VERDICT: PASS | CHANGES | BLOCK' and 'RISK: LOW | MEDIUM | HIGH'")
        verdicts[name] = (v, r)
    worst = max(RISKS.index(r) for _, r in verdicts.values())
    st["risk"] = RISKS[max(worst, RISKS.index(st["risk"]) if st.get("risk") in RISKS else 0)]
    if any(v == "BLOCK" or r == "HIGH" for v, r in verdicts.values()):
        return "NEEDS_HUMAN", "a reviewer blocked or rated HIGH risk"
    changes = [n for n, (v, _) in verdicts.items() if v == "CHANGES"]
    if changes:
        if st["round"] >= st["round_limit"]:
            return "NEEDS_HUMAN", f"review rounds exhausted; outstanding CHANGES: {', '.join(changes)}"
        st["round"] += 1
        st["phase"] = "revise"
        return "BUILD", f"changes requested by {', '.join(changes)}: builder resumed for round {st['round']}"
    limit = RISKS.index(cfg["auto_merge"]["max_risk"])
    if RISKS.index(st["risk"]) <= limit:
        return "MERGE_PENDING", f"all reviews PASS, risk {st['risk']}"
    return "NEEDS_HUMAN", f"risk {st['risk']} is above auto_merge.max_risk {cfg['auto_merge']['max_risk']}"


DECIDERS = {"INTAKE": decide_intake, "SPEC": decide_spec, "BUILD": decide_build, "REVIEW": decide_review}
