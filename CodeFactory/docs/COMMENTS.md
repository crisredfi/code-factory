# GitHub comments: what the factory writes, and where

The factory posts comments automatically, when a job moves to a state that people need to know about.
It does not comment on every step. A comment should answer one question for the reader: what happened,
and what, if anything, they need to do.

The code that writes these comments is `scripts/cf/notify.py`. Every comment is built there from the
job's own files, so the text is always traceable to a file in `jobs/<job-id>/`.

## When it posts

| Move | Where | Heading | Who needs to act |
|---|---|---|---|
| INTAKE → SPEC (accepted) | issue | `### Factory: accepted` | nobody |
| INTAKE → REJECTED | issue | `### Factory: not building this` | nobody |
| INTAKE → NEEDS_INFO | issue | `### Factory: needs more information` | the author answers on the issue |
| any → NEEDS_HUMAN | PR if there is one, else issue | `### Factory: needs a human` | a maintainer |
| REVIEW → MERGE_PENDING | PR | `### Factory: ready to merge` | a maintainer merges, or auto-merge does |
| MERGE_PENDING → COMPLETE | PR | `### Factory: merged` | nobody |
| SPLIT_REVIEW → WAITING_CHILDREN | parent issue | `### Factory: sub-issues created` | nobody |
| WAITING_CHILDREN → COMPLETE | parent issue | `### Factory: all sub-issues complete` | nobody |
| parked job → REJECTED | issue | `### Factory: rejected` | nobody |

It does **not** comment on: gate failures, review rounds, builder phases, subtask waves, or the
move from QUEUED to INTAKE. Those stay in the job folder and on the dashboard. A gate failure that
keeps the job going is not news to the issue's author; one that exhausts the rounds is, and that
arrives as NEEDS_HUMAN.

Sub-issue drafts are **not** posted before a human approves the split. Their titles appear on the
parent only after they exist on GitHub.

## Format

Every comment has the same shape, so a reader can tell at a glance that it came from the factory:

```
<!-- factory -->
### Factory: accepted

<the content, in 3 to 5 short bullets or a short numbered list>

<sub>issue-12 · SPEC · round 1 of 3 · scope small · risk LOW</sub>
```

- The first line is the marker `<!-- factory -->`. It is invisible when rendered. The factory uses it to
  recognise its own comments, and no other tool should write it.
- The heading is one of the headings in the table above. Do not invent new ones.
- The footer is one line in small type: job id, state, round, and the facts a reader may need.
- A comment is at most 3,500 characters. Longer content is cut and ends with `… (full text in the job folder)`.

## Content, per move

- **accepted**: the intake's `## Summary` bullets. Then `Scope`, `Risk`, and the reviewers that will run.
- **not building this**: the intake's `## Summary`, which says why in plain words.
- **needs more information**: the intake's `## Questions`, numbered. End with: "Reply on this issue and the factory will pick it up again."
- **needs a human**: the reason in one or two sentences, and what the maintainer can do: merge, send feedback, or reject.
- **ready to merge**: the risk, and each reviewer with its verdict, one line each.
- **merged**: one line: "Merged by the factory." Nothing else.
- **sub-issues created**: the list of new issues, `#n title`, in dependency order.
- **all sub-issues complete**: one line naming the sub-issues.
- **rejected**: the reason from the rejection, as entered by the maintainer.

## Tone and style

- Write for the issue's author, who may not know the factory exists. Plain, short, polite.
- Say what happened, then what happens next. No jargon: say "tests" and "review", not "gate" or "round".
- Refer to people by their handle only when it helps. Never quote the issue body back at the author.
- Never paste raw logs, stack traces or gate output. A comment links to nothing outside GitHub, because the
  factory's files are local. If something needs to be read in full, say where it is in the job folder.
- Never include secrets, tokens, paths on the machine, or other people's personal data, even when the
  source text has them.

## Limits and safety

- One comment per move. The factory records each posted comment in `state.json` (`notified`), so a rerun
  does not post twice.
- A comment that fails to post does not stop the move. The error goes to `jobs/<job-id>/logs/comments.log`,
  and the job continues.
- Turn all comments off with `github.comments` set to `false`. Turn them on again the same way.
- Comments are posted as the account the `gh` CLI is logged in as. Use a bot or service account if the
  comments should not look like they come from a person.
- The rule about drafting GitHub issues before creating them still applies to issues. Comments are the
  exception the maintainer chose: they post automatically.
