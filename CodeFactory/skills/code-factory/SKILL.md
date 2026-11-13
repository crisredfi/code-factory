---
name: code-factory
description: Operate the CodeFactory. Syncs GitHub issues labelled "factory" into a priority queue, ticks jobs through intake, spec, build (with specialists or sub-issue splits), gate, review and merge, and opens the dashboard. Use when the user asks to run, tick, check, resume or reprioritise the factory, to open its dashboard, or to change reviewers or settings.
---

# CodeFactory skill

The factory is a state machine. **`CodeFactory/scripts/factory.py` decides every state change.** You
run commands and report. Never edit `state.json`, `queue/queue.json` or `BOARD.md` by hand, and never
work around a refused gate. Fix its cause or ask the user.

Paths below are from the repository root. The controller is `CodeFactory/scripts/factory.py`.

## Commands you use

```bash
python3 CodeFactory/scripts/factory.py doctor              # first run, or after config changes
python3 CodeFactory/scripts/factory.py sync                # pull `factory` issues into the queue
python3 CodeFactory/scripts/factory.py tick                # reconcile, start queued jobs, step active jobs
python3 CodeFactory/scripts/factory.py tick --dry-run      # what would happen, changes nothing
python3 CodeFactory/scripts/factory.py status              # the board in the terminal
python3 CodeFactory/scripts/factory.py show <job>          # one job, its files and its next step
python3 CodeFactory/scripts/factory.py step <job> [--dry-run]    # run the job's agents for its stage, then advance
python3 CodeFactory/scripts/factory.py run <job> --stage review.security --agent <agent> --model <id>
python3 CodeFactory/scripts/factory.py advance <job>       # apply the gate for the current stage
python3 CodeFactory/scripts/factory.py prioritize <job> P0 # P0..P3
python3 CodeFactory/scripts/factory.py move <job> up|down  # reorder the queue
python3 CodeFactory/scripts/factory.py reviewers <job> code security ui   # this job's reviewers
python3 CodeFactory/scripts/factory.py reviewers <job> --reset            # back to the defaults + intake
python3 CodeFactory/scripts/factory.py config get
python3 CodeFactory/scripts/factory.py config set reviewers.defaults architecture,code,security
python3 CodeFactory/scripts/factory.py serve --port 8765   # dashboard on 127.0.0.1 (keep running)
```

`feedback <job>`, `reject <job>`, `split <job> approve|cancel`, `merge <job>` and `set <job> <STATE>`
take their text on stdin or as arguments. Use `--dry-run` first where it exists.

## The loop

1. `doctor`. If it reports a FAIL, tell the user which line and stop.
2. `sync`, then `tick`. A tick starts queued jobs up to `max_parallel_jobs` and steps each active job.
   Report the output in one short table: job, state, what happened.
3. When a job needs a human (`NEEDS_HUMAN`, `NEEDS_INFO`, `SPLIT_REVIEW`, `MERGE_PENDING`), say what it
   is waiting for and what the user can do: the dashboard buttons or the commands above.
4. Offer the dashboard (`serve`) for editing: priorities, reviewers per job, settings and the queue.

## How a job moves

| State | What runs | Gate to the next state |
|---|---|---|
| INTAKE | intake agent: ACCEPT / NEEDS-INFO / REJECT, risk, scope, `REVIEWERS_ADD` / `REVIEWERS_DROP` | ACCEPT creates the worktree and moves to SPEC; HIGH risk goes to NEEDS_HUMAN |
| SPEC | spec-writer: `spec.md` with `- [ ]` criteria | at least one criterion |
| BUILD, plan | builder writes `build/plan.json`: mode `single`, `subtasks` or `split`, specialists from the catalog | the plan validates; `split` goes to SPLIT_REVIEW |
| BUILD, subtasks | the controller runs the specialists in dependency waves | — |
| BUILD, integrate / revise | builder commits, pushes, opens a draft PR, writes `build.md` | `checks.sh` passes in the worktree and no protected paths changed; FAIL goes back to BUILD while rounds remain |
| REVIEW | every reviewer in the job's set, in parallel, writes `round-N/review-<name>.md` | all PASS and risk ≤ `auto_merge.max_risk` → MERGE_PENDING; CHANGES → BUILD; BLOCK or HIGH → NEEDS_HUMAN |
| MERGE_PENDING | nothing automatic (unless `auto_merge.enabled`) | `merge <job>` (squash) → COMPLETE |
| SPLIT_REVIEW | nothing: the human reviews the drafts in `build/split.json` | `split <job> approve` creates the sub-issues (labelled `factory`, with `Parent: #n`) → WAITING_CHILDREN |
| WAITING_CHILDREN | nothing: the sub-issues run through the queue | all children COMPLETE → COMPLETE |

## Reviewers

- Every job runs `reviewers.defaults` from config, plus what intake adds, minus what intake drops.
  `reviewers.always` cannot be dropped.
- A human can set a job's reviewers with `reviewers <job> ...` or the dashboard. That overrides the defaults
  and intake for that job. `--reset` removes the override.
- `ui` and `ux` run when the issue has `factory:ui-review` or `factory:ux-review`, or intake adds them.
- Never add a reviewer on your own initiative. Ask the user, or let intake decide.

## Builder decisions

The builder chooses the agents. It can only pick specialists from `agents.catalog` in config, and the
controller refuses anything else. If the builder proposes a split, the drafts wait in SPLIT_REVIEW until a
human approves them. Nothing is created on GitHub before that.

## Rules

- Never merge unless the user asked for this job's merge in this session, or `auto_merge.enabled` is true.
  The factory's own auto-merge only applies at or below `auto_merge.max_risk`.
- Never create GitHub issues except through `split <job> approve` after a human has approved the drafts. Comments are posted by the controller automatically, per docs/COMMENTS.md; never post one by hand unless the user asks.
- Never push to the base branch. Builder branches are `factory/<job-id>`.
- Never change `factory/checks.sh` or `factory/config.json` in the target repo from the factory; those are protected.
- Never set `--force` on a transition unless the user asks.
- Report the state and the next step for each job you touched, in one short table.
- When something fails, quote the failing line from `show <job>` or the log under `jobs/<job-id>/logs/`.
