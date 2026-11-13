"""GitHub comments for the moves listed in docs/COMMENTS.md. Each comment is built from the job's files.

Called from state.set_state after every save. A comment that cannot be posted never blocks the move:
the error goes to the job's logs/comments.log.
"""
from __future__ import annotations

from . import config, github, state, util
from .util import FactoryError

MAX_CHARS = 3500


def _summary(job_id: str, rel: str = "intake.md") -> list[str]:
    return [l for l in util.section(state.read_doc(job_id, rel), "Summary").splitlines() if l.strip()]


def _questions(job_id: str) -> list[str]:
    return [l for l in util.section(state.read_doc(job_id, "intake.md"), "Questions").splitlines() if l.strip()]


def _footer(st: dict) -> str:
    parts = [st["job_id"], st["state"]]
    if st.get("round"):
        parts.append(f"round {st['round']} of {st['round_limit']}")
    if st.get("scope"):
        parts.append(f"scope {st['scope']}")
    if st.get("risk"):
        parts.append(f"risk {st['risk']}")
    return "<sub>" + " · ".join(parts) + "</sub>"


def _body(heading: str, lines: list[str], st: dict) -> str:
    text = f"<!-- factory -->\n### Factory: {heading}\n\n" + "\n".join(lines).strip() + f"\n\n{_footer(st)}\n"
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 60].rstrip() + "\n\n… (full text in the job folder)\n\n" + _footer(st) + "\n"
    return text


def render(st: dict, frm: str, to: str, note: str):
    """Return (target, heading, lines) for a move that deserves a comment, or None.

    target is ("issue", number) or ("pr", url). The heading and lines are turned into the comment by _body.
    """
    job = st["job_id"]
    issue = ("issue", st["issue"])
    pr = ("pr", st["pr_url"]) if st.get("pr_url") else issue

    if frm == "INTAKE" and to == "SPEC":
        reviewers = ", ".join(st.get("reviewers", [])) or "none"
        return issue, "accepted", _summary(job) + [
            "", f"- **Scope:** {st.get('scope') or 'n/a'}",
            f"- **Risk:** {st.get('risk')}",
            f"- **Reviewers:** {reviewers}",
        ]
    if frm == "INTAKE" and to == "REJECTED":
        return issue, "not building this", _summary(job)
    if frm == "INTAKE" and to == "NEEDS_INFO":
        qs = _questions(job)
        return issue, "needs more information", (qs or ["- Please add more detail to the issue."]) + [
            "", "Reply on this issue and the factory will pick it up again."]
    if to == "NEEDS_HUMAN":
        return pr, "needs a human", [note[:400], "",
                                     "Next: merge the PR, send feedback from the dashboard, or reject the job."]
    if frm == "REVIEW" and to == "MERGE_PENDING":
        lines = [f"- **Risk:** {st.get('risk')}", "- **Reviewers:**"]
        lines += [f"  - {r}" for r in st.get("reviewers", [])]
        lines += ["", "A maintainer merges this PR, or auto-merge does, depending on the factory settings."]
        return pr, "ready to merge", lines
    if frm == "MERGE_PENDING" and to == "COMPLETE":
        return pr, "merged", ["Merged by the factory."]
    if frm == "SPLIT_REVIEW" and to == "WAITING_CHILDREN":
        kids = [f"- #{n}" for n in st.get("children", [])]
        return issue, "sub-issues created", ["These sub-issues were created and will run through the factory:", ""] + kids
    if frm == "WAITING_CHILDREN" and to == "COMPLETE":
        kids = [f"- #{n}" for n in st.get("children", [])]
        return issue, "all sub-issues complete", ["Every sub-issue is complete:", ""] + kids
    if to == "REJECTED" and frm in {"NEEDS_INFO", "NEEDS_HUMAN", "SPLIT_REVIEW", "MERGE_PENDING"}:
        reason = state.read_doc(job, "rejection.md").split("\n", 2)[-1].strip() or note
        return issue, "rejected", [reason[:600]]
    return None


def transitioned(st: dict, frm: str, to: str, note: str) -> None:
    cfg = config.load()
    if not cfg["github"].get("comments"):
        return
    planned = render(st, frm, to, note)
    if planned is None:
        return
    key = f"{to}-{len(st['history'])}"
    if key in st.get("notified", []):
        return
    (kind, target), heading, lines = planned
    try:
        github.post_comment(kind, target, _body(heading, lines, st))
    except FactoryError as e:
        log = state.job_dir(st["job_id"]) / "logs" / "comments.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(f"{util.now()} {kind} {target} '{heading}': {e}\n")
        return
    st["notified"] = st.get("notified", []) + [key]
    state.save_state(st)
