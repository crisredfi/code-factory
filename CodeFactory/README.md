# CodeFactory

A queue-driven pipeline that turns GitHub issues labelled `factory` into draft pull requests, checks
them with a deterministic gate and reviewers, and either marks them ready to merge or hands them to a
human. v2: the intake chooses reviewers, the builder chooses specialists or splits work into
sub-issues, and an editable dashboard runs the factory.

- Brief and decisions: [docs/PROMPT.md](docs/PROMPT.md)
- States, gates and rounds: [docs/STATES.md](docs/STATES.md)
- What the factory comments on GitHub, and how: [docs/COMMENTS.md](docs/COMMENTS.md)
- The procedure Claude follows: [skills/code-factory/SKILL.md](skills/code-factory/SKILL.md)

## Layout

```
CodeFactory/
  INSTALL.md               install guide: requirements, links, checks, first run
  factory.config.json      parallelism, rounds, reviewers, agent catalog, runner, auto-merge
  scripts/factory.py       entry point (thin); the logic is in the cf/ package
  scripts/cf/              one job per module: paths, model, util, config, state, reviewers,
                           gate, decide, agents, github, lifecycle, steps, queue, report, api, cli
  docs/COMMENTS.md         what the factory posts on GitHub, where, and in what format
  templates/checks/        checks.sh templates: generic, ios, node, python
  dashboard/index.html     the editable dashboard, served by `factory.py serve`
  agents/                  one markdown file per agent (intake, spec-writer, builder,
                           reviewer-*, ios/backend/os/test engineers)
  skills/code-factory/     the skill that drives the factory from Claude Code
  queue/queue.json         index of all jobs (generated)
  jobs/<job-id>/           per-job memory: state.json, issue.md, intake.md, spec.md, build.md,
                           build/plan.json, build/split.json, build/subtasks/, round-N/, logs/
  BOARD.md                 text board (generated)
  tests/test_factory.py    unit, git-gate and API tests
```

## Setup (once)

The full procedure is in [INSTALL.md](INSTALL.md). The short version:


Make the agents and the skill visible to Claude Code from the repository root (already done in this
workspace):

```bash
mkdir -p .claude/skills .claude/agents
ln -s ../../CodeFactory/skills/code-factory .claude/skills/code-factory
for f in CodeFactory/agents/*.md; do ln -sf ../../"$f" .claude/agents/"$(basename "$f")"; done
```

Then set the target in `factory.config.json`:
- `target_repo`: absolute path of the repository the factory builds in. It must contain `factory/checks.sh`
  (the gate script) and an `origin` remote for `base_branch`.
- `github.repo`: `owner/name`, or empty to use the target's remote.
- `runner.extra_args`: review `--permission-mode acceptEdits` before a real run.

Check it:

```bash
python3 CodeFactory/scripts/factory.py doctor
```

## Daily use

```bash
python3 CodeFactory/scripts/factory.py serve --port 8765   # open http://127.0.0.1:8765
python3 CodeFactory/scripts/factory.py sync
python3 CodeFactory/scripts/factory.py tick                # start jobs, step active jobs
```

Everything the dashboard can do is also a command. In the dashboard you can:
- reorder the queue with ▲ ▼ and change a job's priority;
- toggle a job's reviewers with the chips (code always runs);
- step a job now, merge a MERGE_PENDING PR, approve or cancel a split, send feedback, reject, or move a job by hand;
- edit the settings: parallel jobs, subtasks, rounds, default reviewers, auto-merge.

Every action that touches GitHub or moves a job asks for confirmation first.

## GitHub labels and issue conventions

| Label or text | Meaning |
|---|---|
| `factory` | put the issue in the queue |
| `priority:P0` … `priority:P3` | queue priority (default P2) |
| `factory:ui-review`, `factory:ux-review` | run the UI or UX reviewer for this issue |
| `factory:skip` | ignore this issue |
| `Parent: #n` in the body | a sub-issue created from a split; waits for its parent's split |
| `Depends on: #n, #m` in the body | not started until those issues are COMPLETE |

## Safety defaults

- Auto-merge is **off**. A job that passes review waits in MERGE_PENDING until someone merges it.
- Sub-issues are drafts until a human approves the split in the dashboard or with `split <job> approve`.
- The gate runs the base checkout's `factory/checks.sh`, not the branch's copy, and fails any branch that
  changes protected paths.
- The dashboard binds to 127.0.0.1 only.
- The factory never deploys, and never pushes to the base branch.

## Known limits (v2)

- Runner is the `claude` CLI in print mode. Other runners need an adapter.
- Cost and token tracking is not built yet (the reference design reads Claude Code transcripts for it).
- Merging does not sync the branch with the base first. A branch behind the base needs a human.
- `tick` runs in the foreground from the terminal; the dashboard starts it in the background.
- NEEDS_INFO is picked up by `sync` (run it, or let `tick` run it). An answer from the issue author or a trusted commenter sends the job back to INTAKE.

## Tests

```bash
python3 -m unittest discover -s CodeFactory/tests -v
```

The suite covers the queue, reviewer selection, intake and spec gates, plan validation, split and
sub-issue flow, a real git worktree and gate run on a local bare remote, review loops, human feedback,
rejection, parent reconciliation, and the HTTP API.
