# Examples

Worked examples of a job folder, so you can see what each stage writes and which gate reads it.
Nothing here is run by the factory. They are copies of real file shapes, and the test suite
(`tests/test_examples.py`) runs the controller's own parsers over them, so they cannot drift from the code.

The GitHub links point to a made-up organisation (`acme`). They are placeholders.

## The three jobs

| Job | What it shows |
|---|---|
| [001-add-subtract](jobs/001-add-subtract/) | The full path for a small, low-risk change: intake accepts, spec, one subtask, gate passes, three reviews, ready to merge |
| [002-split-into-sub-issues](jobs/002-split-into-sub-issues/) | A large change: the builder proposes a split, the job waits in SPLIT_REVIEW until a human approves the drafts |
| [003-needs-info-then-resumed](jobs/003-needs-info-then-resumed/) | Questions to the author, an answer, and the job resuming at INTAKE with the old decision kept as `intake-1.md` |

## What each file is

| File | Written by | Read by |
|---|---|---|
| `issue.md` | sync (from GitHub; body and trusted comments, quoted) | intake, every agent |
| `intake.md` | intake agent | `decide_intake`: DECISION, RISK, SCOPE, REVIEWERS_ADD/DROP; its Summary and Questions become the issue comment |
| `intake-N.md` | the controller, when a question is answered (renamed from `intake.md`) | the next intake, so it does not ask again |
| `spec.md` | spec-writer | `decide_spec`: needs at least one `- [ ]` criterion |
| `build/plan.json` | builder, phase plan | `validate_plan`: mode, specialists from the catalog, dependency waves |
| `build/split.json` | builder, mode split | `validate_split`: at least two issues, each with id, title and body |
| `build/subtasks/<id>.md` | specialists | the builder, integrate phase |
| `build.md` | builder, integrate and revise phases | `decide_build`: STATUS, and DRAFT_PR for the PR link |
| `round-N/checks.md` | the gate (`gate.run_gate`) | the builder in revise, the reviewers; `GATE: PASS` or `FAIL` and the commit it ran on |
| `round-N/review-<name>.md` | each reviewer | `decide_review`: VERDICT and RISK |
| `state.json` | the controller only | the dashboard, `show`, the history |
| `feedback-N.md`, `rejection.md` | the controller, from a human | the builder (feedback), the comment (rejection) |

`comments.md` shows what the factory posts on GitHub for each move. See [docs/COMMENTS.md](../docs/COMMENTS.md).
