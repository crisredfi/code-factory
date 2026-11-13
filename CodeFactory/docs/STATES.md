# States, gates and rounds (v2)

The controller (`scripts/factory.py`) is the only thing that changes a job's state. Every
transition is validated against `TRANSITIONS` and recorded in the job's `state.json` history.

## Columns

| State | Column | Active (takes a slot)? | Who works | Leaves when |
|---|---|---|---|---|
| QUEUED | Queue | no | — | started by `next` / `tick` (priority, then queue order, blocked jobs skipped) |
| INTAKE | Intake | yes | intake agent | `DECISION` line in `intake.md` |
| NEEDS_INFO | Needs info | no | issue author | sync sees a trusted reply and resumes the job at INTAKE (if a slot is free) |
| SPEC | Spec | yes | spec-writer | at least one `- [ ]` criterion in `spec.md` |
| BUILD | Build | yes | builder, specialists, gate | `STATUS` in `build.md`; plan validates; gate passes |
| REVIEW | Review | yes | every reviewer in the job's set | all verdicts in, `BLOCK` / `CHANGES` / PASS decided |
| SPLIT_REVIEW | Split review | no | human | `split approve` (→ WAITING_CHILDREN) or `split cancel` (→ NEEDS_HUMAN) |
| WAITING_CHILDREN | Waiting for sub-issues | no | sub-issue jobs | every child COMPLETE |
| NEEDS_HUMAN | Needs human | no | human | feedback (→ BUILD), merge, reject, or `set` |
| MERGE_PENDING | Merge pending | no | human, or the factory when `auto_merge.enabled` | `merge` → COMPLETE |
| COMPLETE | Complete | no | — | terminal |
| REJECTED | Rejected | no | — | terminal |
| FAILED | Failed | no | human | `set` |

## Gates

```
QUEUED ──next──▶ INTAKE ──ACCEPT, risk≠HIGH (worktree created)──▶ SPEC ──criteria──▶ BUILD
   │                ├─NEEDS-INFO──▶ NEEDS_INFO                       │
   │                ├─REJECT──────▶ REJECTED                         │ plan: mode split
   │                └─ACCEPT, HIGH─▶ NEEDS_HUMAN                     ├──────▶ SPLIT_REVIEW ─approve▶ WAITING_CHILDREN ─children done▶ COMPLETE
   │                                                                 │
   │      plan / subtasks / integrate ──gate PASS──▶ REVIEW ◀────────┤
   │                                                    │            │
   │         gate FAIL (rounds left) ──────────────────▶ BUILD (revise)
   │                                                    │
   │                  CHANGES (rounds left) ───────────▶ BUILD (revise)
   │                                                    │
   │       BLOCK / HIGH risk / rounds exhausted ───────▶ NEEDS_HUMAN
   │                                                    │
   │       all PASS, risk ≤ auto_merge.max_risk ──────▶ MERGE_PENDING ──merge──▶ COMPLETE
   └─ NEEDS_HUMAN: feedback ──▶ BUILD with a fresh round budget
```

## Rounds

`round` is the current build round (1 after SPEC). Each return to BUILD after a failed gate or a
CHANGES verdict adds one. `round_limit` is `max_rounds` from config, and it is reset by human feedback
to `round + max_rounds`. Each round has its own folder, `round-N/`, with `checks.md`, `checks.log`
and `review-<name>.md`. Earlier rounds are kept as they were.

## Phases inside BUILD

`phase` says what the builder is doing next. The controller sets it, and `step` runs it.

| phase | Runs | Then |
|---|---|---|
| plan | builder writes `build/plan.json` | `plan` validates; `split` → SPLIT_REVIEW; otherwise phase = subtasks |
| subtasks | specialists in dependency waves (`build/subtasks/<id>.md`) | phase = integrate |
| integrate | builder integrates, pushes, opens the draft PR, writes `build.md` | gate |
| revise | builder fixes what checks or reviewers asked for | gate |

## The gate (deterministic, not an agent)

For the current round, in the job's worktree:
1. the worktree is clean and has commits ahead of `origin/<base>`;
2. no changed file is under `protected_paths`;
3. `<target repo>/<checks_command>` (the base checkout's copy, not the branch's) exits 0 when run in the worktree.

Result in `round-N/checks.md`: `GATE: PASS | FAIL` with the commit it was run on.

## Risk

Job risk is the highest of the intake rating and every reviewer's RISK. Auto-merge needs risk at or
below `auto_merge.max_risk` (LOW by default) and `auto_merge.enabled` to be true.

## Parallelism

- `max_parallel_jobs` caps the active jobs (INTAKE, SPEC, BUILD, REVIEW).
- `max_parallel_subtasks` caps the agents running at once inside a job (subtask waves and reviewers).
- A per-job file lock means only one `step` runs on a job at a time, even across processes.
