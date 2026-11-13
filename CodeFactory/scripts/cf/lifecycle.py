"""Human-driven moves: merge, sub-issue split, reject, feedback, priority, queue order, reviewer override.

Each function validates the move, performs its side effects through github.py, and saves state.
"""
from __future__ import annotations

from pathlib import Path

from . import gate, github, reviewers, state, util
from .decide import validate_split
from .model import PRIORITIES, PARKABLE
from .util import FactoryError, waves


def merge_job(st: dict, cfg: dict, dry: bool) -> str:
    if st["state"] != "MERGE_PENDING":
        raise FactoryError(f"{st['job_id']} is {st['state']}, not MERGE_PENDING")
    if not st.get("pr_url"):
        raise FactoryError("no PR recorded for this job")
    method = cfg["github"]["merge_method"]
    if dry:
        return "DRY RUN: " + " ".join(github.merge_command(st["pr_url"], method))
    github.merge_pr(st["pr_url"], method)
    wt = Path(st["worktree"]) if st.get("worktree") else None
    if wt and wt.exists():
        util.git(gate.target(cfg), "worktree", "remove", "--force", str(wt), check=False)
    state.set_state(st, "COMPLETE", f"merged {st['pr_url']}")
    reconcile(cfg)
    return f"merged {st['pr_url']}"


def approve_split(st: dict, cfg: dict, dry: bool) -> str:
    """Create the drafted sub-issues on GitHub, in dependency order, and wait for them."""
    if st["state"] != "SPLIT_REVIEW":
        raise FactoryError(f"{st['job_id']} is {st['state']}, not SPLIT_REVIEW")
    issues = validate_split(st["job_id"])
    numbers: dict[str, int] = {}
    created = []
    for wave in waves(issues):
        for item in wave:
            deps = [numbers[d] for d in item.get("depends_on", [])]
            dep_line = "Depends on: " + ", ".join(f"#{n}" for n in deps) + "\n" if deps else ""
            body = f"Parent: #{st['issue']}\n{dep_line}\n{item['body']}"
            if dry:
                created.append(f"DRY RUN: create issue {item['title']!r} (depends on {deps or 'none'})")
                numbers[item["id"]] = 0
                continue
            numbers[item["id"]] = github.create_issue(cfg, item["title"], body)
            created.append(f"#{numbers[item['id']]} {item['title']}")
    if dry:
        return "\n".join(created)
    st["children"] = [numbers[i["id"]] for i in issues]
    state.set_state(st, "WAITING_CHILDREN", f"created sub-issues {', '.join('#' + str(n) for n in st['children'])}")
    return "\n".join(created)


def cancel_split(st: dict) -> None:
    if st["state"] != "SPLIT_REVIEW":
        raise FactoryError(f"{st['job_id']} is {st['state']}, not SPLIT_REVIEW")
    state.set_state(st, "NEEDS_HUMAN", "sub-issue split cancelled")


def reconcile(cfg: dict) -> None:
    """A parent waiting on children completes once every child job is COMPLETE."""
    states = state.all_states()
    by_parent: dict[int, list[dict]] = {}
    for s in states:
        if s.get("parent"):
            by_parent.setdefault(s["parent"], []).append(s)
    for s in states:
        if s["state"] == "WAITING_CHILDREN":
            kids = by_parent.get(s["issue"], [])
            if kids and all(k["state"] == "COMPLETE" for k in kids):
                state.set_state(s, "COMPLETE", f"all {len(kids)} sub-issues complete")


def reject_job(st: dict, cfg: dict, reason: str, dry: bool = False) -> None:
    if st["state"] not in PARKABLE:
        raise FactoryError(f"{st['job_id']} is {st['state']}; only parked jobs can be rejected")
    if not reason.strip():
        raise FactoryError("a reason is required")
    (state.job_dir(st["job_id"]) / "rejection.md").write_text(f"REJECTED\n\n{reason.strip()}\n")
    if st.get("pr_url") and not dry:
        github.close_pr(st["pr_url"])
    state.set_state(st, "REJECTED", "rejected: " + reason.strip()[:120])


def feedback_job(st: dict, cfg: dict, text: str) -> None:
    if st["state"] != "NEEDS_HUMAN":
        raise FactoryError(f"{st['job_id']} is {st['state']}, not NEEDS_HUMAN")
    if not text.strip():
        raise FactoryError("no feedback text")
    d = state.job_dir(st["job_id"])
    k = len(list(d.glob("feedback-*.md"))) + 1
    (d / f"feedback-{k}.md").write_text(f"# Feedback {k}\n\n{text.strip()}\n")
    st["round"] += 1
    st["round_limit"] = st["round"] + cfg["max_rounds"]
    st["phase"] = "revise"
    state.set_state(st, "BUILD", f"human feedback {k}: new round budget")


def set_priority(job_id: str, prio: str) -> None:
    if prio not in PRIORITIES:
        raise FactoryError(f"priority must be one of {PRIORITIES}")
    st = state.load_state(job_id)
    st["priority"] = prio
    st["history"].append({"at": util.now(), "from": st["state"], "to": st["state"], "note": f"priority -> {prio}"})
    state.save_state(st)
    state.render_board()


def move_job(job_id: str, direction: str) -> str:
    """Swap a queued job with its neighbour. Moving past a neighbour adopts its priority."""
    from .queue import queued_sorted  # local import: queue depends on lifecycle
    rows = list(state.load_index()["jobs"].values())
    queued = queued_sorted(rows)
    ids = [r["job_id"] for r in queued]
    if job_id not in ids:
        raise FactoryError(f"{job_id} is not queued")
    i = ids.index(job_id)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(queued):
        return "already at the edge"
    a, b = state.load_state(job_id), state.load_state(queued[j]["job_id"])
    a["queue_pos"], b["queue_pos"] = b["queue_pos"], a["queue_pos"]
    a["priority"] = b["priority"]
    state.save_state(a)
    state.save_state(b)
    state.render_board()
    return f"{job_id} moved {direction}"


def set_reviewers(job_id: str, cfg: dict, names: list[str] | None) -> list[str]:
    st = state.load_state(job_id)
    if st["state"] in {"COMPLETE", "REJECTED"}:
        raise FactoryError(f"{job_id} is {st['state']}")
    st["reviewers_override"] = names
    st["reviewers"] = reviewers.review_names(st, cfg)  # validates names; always-on reviewers are kept
    state.save_state(st)
    return st["reviewers"]
