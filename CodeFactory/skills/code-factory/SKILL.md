---
name: code-factory
description: Operate the CodeFactory. Runs the queue of GitHub issues labelled "factory" through intake, spec, build, gate, review and merge; resumes jobs; answers questions about them; gives feedback, rejects, approves splits and merges; and opens the dashboard. Use for /factory run, /factory run <n>, /factory followed by anything (a question, feedback, reviewers, a new feature), or when the user asks about the factory queue, a job, or its settings.
---

# CodeFactory

You are the operator of the CodeFactory. You run its controller, read its files, report what it
did, and carry out the human's decisions through its commands. You do not write code, specs, reviews
or decisions yourself. Each stage's agent writes its own file, and the controller checks it.

Repository root is the working directory. The controller is `CodeFactory/scripts/factory.py`.
Everything below is a command you can run; nothing here needs a GUI.

## How this differs from a session-driven factory

In a factory where the orchestrating session spawns every agent, the session carries the whole run in
its context. Here the **controller** does that work. `tick` and `step` start each stage's agents in
parallel, wait for them, apply the gate and save the state. You only decide when to tick, what to
tell the human, and which command carries out their decision. So you never spawn an agent, never
edit a `state.json`, and never advance a job by hand past a gate. If a command refuses, the message
says why. Fix the cause, or tell the human.

## Modes

```
/factory run              choose the models for this run (walkthrough below), then drain the queue:
                          sync, then tick until nothing is active or running
/factory run <n>          run one issue to its next parked state (needs-human, needs-info, merge-pending, …)
/factory <anything else>  talk to the factory: status, a question, feedback, reject, reviewers,
                          priority, settings, or a new feature
```

If the user's words do not clearly match one of these, ask which job they mean before acting.

## The master session

The session this skill runs in is the master. It runs on the model the human selected for it, and that
model orchestrates the run. Never ask about it, never change it, and never write it into a run profile.
The factory has no stage for it, so `profile set` refuses any stage name that is not a stage of the pipeline.

## Model walkthrough (every /factory run)

Before `tick`, let the human choose which model each stage runs on. This is the only place the run's
models are chosen. Do it in this order:

1. Run `models`. It lists the Claude models in the config and the local models installed in LM Studio, and
   marks the local models that are loaded now. Do not paste the whole output; summarise the choices.
2. Ask with AskUserQuestion: **"Use the config's models for this run, or choose per stage?"**
   - Config's models (recommended): run `profile clear`, then go to step 5.
   - Choose per stage: go to step 3.
3. Ask one AskUserQuestion per group, in this order. Offer up to four options from the `models` output; the
   human can type any other key under "Other".
   - **Build** (`build`): the builder's plan, integrate and revise phases.
   - **Specialists** (`specialists`): the subtasks the builder launches.
   - **Review** (`review`): every reviewer. A single reviewer can be chosen as `review.<name>`, if the human asks.
   - **Intake and spec** (`intake`, `spec`): the first two stages. Ask about them as one question, or as two if
     the human wants them separate.
4. Show the result with `profile show` and ask one more question: **"Start the run with this?"** If the human
   changes something, set it again.
5. Save the choices with `profile set`, one `stage=model` per argument. Use the exact keys from `models`,
   for example `build=local:qwen3.6-35b-a3b-splash review=claude-opus-5-5`. Then continue with `/factory run`.

A local model is a model in LM Studio. It is written `local:<key>`, and the key must be the one `models`
shows. `profile set` checks that the model is installed and refuses it otherwise.

**What the run does with local models.** Before a stage runs on a local model, the controller checks what LM
Studio has loaded. If the wanted model is already loaded, it keeps it. Otherwise it unloads every other loaded
model and then loads the wanted one. A Claude stage does not touch LM Studio. Local stages run one at a time,
because only one model is loaded at a time. Say so to the human when they pick more than one local model.

## Self-healing

The controller watches every running agent. An agent counts as progressing while its processes use CPU, or its
worktree or job folder changes. When nothing moves for `self_heal.stuck_after_s` (`local_stuck_after_s` for a
local model), the watchdog marks the run SUSPECT. If the silence lasts another `self_heal.confirm_s`, the run is
STUCK. The controller kills it and retries it, on `self_heal.fallback_model` if one is set, up to
`self_heal.max_retries` times. Every stuck attempt is recorded in the job's `logs/self-heal.log`.

- A tick that reports `stuck on attempt N` is progress, not an error. Report it as one line and carry on.
- When every attempt is stuck, the job goes to NEEDS_HUMAN with the note `self-heal gave up`. Read
  `logs/self-heal.log` and tell the human what was tried. Their options are `feedback`, `set`, or `reject`.
- Never kill an agent process or delete a `.lock` yourself. The watchdog is the only thing that stops an agent.

## Pipeline

```
QUEUED ─tick─▶ INTAKE ─ACCEPT─▶ SPEC ─criteria─▶ BUILD ─gate PASS─▶ REVIEW ─all PASS─▶ MERGE_PENDING ─merge─▶ COMPLETE
   │             │ │                              ▲ │  ▲                  │
   │             │ └─NEEDS-INFO─▶ NEEDS_INFO ─────┘ │  └─gate FAIL / CHANGES (rounds left)
   │             └─REJECT─▶ REJECTED                 └─ split─▶ SPLIT_REVIEW ─approve─▶ WAITING_CHILDREN ─▶ COMPLETE
   └─ any stage ─ BLOCK, HIGH risk, rounds used up, blocked builder ─▶ NEEDS_HUMAN
```

| State | What runs when you `step` it | Moves on when |
|---|---|---|
| QUEUED | nothing | `tick` or `next` starts it (priority order, blocked jobs skipped, parallel limit respected) |
| INTAKE | intake agent writes `intake.md` | DECISION is ACCEPT, NEEDS-INFO or REJECT. HIGH risk stops at NEEDS_HUMAN |
| NEEDS_INFO | nothing; the author answers on the issue | `sync` (or `tick`) sees a trusted reply and resumes at INTAKE |
| SPEC | spec-writer writes `spec.md` | at least one `- [ ]` criterion |
| BUILD | phases: plan, subtasks, integrate, revise (see below) | gate passes (REVIEW), split proposed (SPLIT_REVIEW), or rounds are used up (NEEDS_HUMAN) |
| REVIEW | every reviewer for this job, in parallel, writes `round-N/review-<name>.md` | all PASS and risk within `auto_merge.max_risk` (MERGE_PENDING); CHANGES goes back to BUILD; BLOCK or HIGH stops at NEEDS_HUMAN |
| SPLIT_REVIEW | nothing; a human reads the drafts in `build/split.json` | `split <job> approve` (creates sub-issues) or `split <job> cancel` |
| WAITING_CHILDREN | nothing; sub-issues run through the queue | every child is COMPLETE |
| MERGE_PENDING | nothing (unless `auto_merge.enabled`) | `merge <job>` after a human asks for it |
| NEEDS_HUMAN | nothing | `feedback`, `merge`, `reject`, or `set` |
| COMPLETE, REJECTED, FAILED | nothing | terminal, except FAILED, which `set` can move |

**BUILD phases.** The builder runs in four phases, and `step` runs them in order:
1. **plan** writes `build/plan.json`: mode `single`, `subtasks` or `split`, with specialists chosen only from the catalog.
2. **subtasks** runs the specialists in dependency waves (`build/subtasks/<id>.md`).
3. **integrate** has the builder finish the work, push, open the draft PR and write `build.md` with `STATUS: READY` or `BLOCKED`.
4. **revise** runs after a failed gate or requested changes. The builder reads `round-N-1/checks.md` or the review files, and changes only what they ask for.

Then the controller runs the gate: no uncommitted changes, commits ahead of the base, no protected paths
touched, and the base checkout's `factory/checks.sh` exits 0 in the job's worktree.

## Commands

Run these from the repository root.

```bash
# health and queue
python3 CodeFactory/scripts/factory.py doctor
python3 CodeFactory/scripts/factory.py sync                 # pull labelled issues; resume answered questions
python3 CodeFactory/scripts/factory.py status               # the board, as text
python3 CodeFactory/scripts/factory.py show issue-<n>       # one job: state, history, what it needs next
python3 CodeFactory/scripts/factory.py tick --dry-run       # what a tick would do; changes nothing
python3 CodeFactory/scripts/factory.py tick                 # start queued jobs, step every active job

# one job
python3 CodeFactory/scripts/factory.py step issue-<n> --dry-run   # show the agents a step would run
python3 CodeFactory/scripts/factory.py step issue-<n>             # run its agents, then apply the gate
python3 CodeFactory/scripts/factory.py plan issue-<n>             # subtask waves from build/plan.json
python3 CodeFactory/scripts/factory.py advance issue-<n>          # apply the gate only (after a manual fix)

# human decisions
python3 CodeFactory/scripts/factory.py feedback issue-<n> < feedback.txt     # NEEDS_HUMAN -> BUILD, new budget
python3 CodeFactory/scripts/factory.py reject issue-<n> < reason.txt        # parked job -> REJECTED, closes PR
python3 CodeFactory/scripts/factory.py split issue-<n> approve --dry-run    # preview sub-issues
python3 CodeFactory/scripts/factory.py split issue-<n> approve              # create them (after the human says yes)
python3 CodeFactory/scripts/factory.py split issue-<n> cancel
python3 CodeFactory/scripts/factory.py merge issue-<n> --dry-run            # preview the merge command
python3 CodeFactory/scripts/factory.py merge issue-<n>                      # squash-merge (after the human says yes)
python3 CodeFactory/scripts/factory.py reviewers issue-<n> code security ui # this job's reviewers
python3 CodeFactory/scripts/factory.py reviewers issue-<n> --reset          # back to defaults + intake
python3 CodeFactory/scripts/factory.py prioritize issue-<n> P0              # P0..P3
python3 CodeFactory/scripts/factory.py move issue-<n> up|down               # queue order
python3 CodeFactory/scripts/factory.py set issue-<n> <STATE> --note "…"     # validated move; --force only when asked

# models for a run
python3 CodeFactory/scripts/factory.py models                              # Claude models and local models in LM Studio
python3 CodeFactory/scripts/factory.py profile show                        # each stage and the model it runs on now
python3 CodeFactory/scripts/factory.py profile set build=local:<key> review=claude-opus-5-5
python3 CodeFactory/scripts/factory.py profile clear                       # back to the config's models

# settings
python3 CodeFactory/scripts/factory.py config get
python3 CodeFactory/scripts/factory.py config set max_parallel_jobs 3
python3 CodeFactory/scripts/factory.py config set reviewers.defaults architecture,code,security
python3 CodeFactory/scripts/factory.py config set auto_merge.enabled false

# dashboard (keep it running in its own terminal)
python3 CodeFactory/scripts/factory.py serve --port 8765
```

`feedback` and `reject` read their text from stdin. Pipe it from a file or a heredoc, and keep the human's
words exactly as they wrote them.

## /factory run

0. The model walkthrough above. A run without it uses the config's models.
1. `doctor`. If it reports a FAIL, stop and tell the human the line and its fix (INSTALL.md, step 7).
   When the run profile uses local models, doctor also checks that `lms` is on the PATH and that the LM Studio
   server answers.
2. `sync`. Report what was added, refreshed and resumed.
3. `tick`. Report its output as one table: job, state before, state after, reason. The tick
   returns when its agents finish, which can take several minutes. Do not poll in a loop. Run `status`
   again when the tick finishes, or when the human asks.
4. Repeat `tick` while anything is in an active state (INTAKE, SPEC, BUILD, REVIEW) and the queue is not empty.
5. When nothing is active or queued, print the list of jobs that wait on a human, with the exact next command for each (see the table below).

Never start the same job twice. The controller takes a per-job lock, and a second `step` on a locked job
refuses with "already being stepped". If the lock is stale (no process is running), say so and ask the
human before removing `jobs/<job>/.lock`.

## /factory run <n>

Run one issue with the same steps, but only for `issue-<n>`:

1. `sync`, then confirm `show issue-<n>` exists. If it does not, the issue is missing the `factory` label, or it is closed. Say which.
2. If the job is QUEUED, start it with `tick --dry-run` to see whether a slot is free, then `tick`.
3. `step issue-<n>` until its state is one that waits (NEEDS_HUMAN, NEEDS_INFO, SPLIT_REVIEW, MERGE_PENDING, COMPLETE, REJECTED).
4. Finish with `show issue-<n>` and its next command.

## /factory <anything>

Work out what the human wants, then do exactly that with the controller. Every change of state goes through
a command. Ask before acting when it is unclear which job they mean, or whether the words are a question,
feedback or a new feature.

**A question** (status, timing, why a job stopped, what a reviewer said): answer from `show`, the board, and the job's files. You may read a whole file when the answer is in it. Quote no more than the sentence you need.

**What needs them:** run `status`, then list each parked job with its reason and command:

| State | Say to the human | Their command |
|---|---|---|
| NEEDS_HUMAN | the reason from `state.json`'s last history note, and the file that explains it | a decision on the PR: `merge`, `feedback` or `reject` |
| NEEDS_INFO | the questions from `intake.md`, and that the author answers on the issue | none. `sync` resumes it after the answer |
| SPLIT_REVIEW | the drafts in `build/split.json`, by title | `split <job> approve` or `split <job> cancel` |
| MERGE_PENDING | the PR, and the reviewers' verdicts | `merge <job>`, or merge it on GitHub yourself |
| WAITING_CHILDREN | which sub-issues it waits for | none |

**Feedback on a NEEDS_HUMAN job:** pass their words exactly, on stdin:

```bash
python3 CodeFactory/scripts/factory.py feedback issue-<n> <<'EOF'
<their feedback, verbatim>
EOF
```

It starts a new round with a fresh budget. Then `tick` continues the job.

**Reject a parked job** (NEEDS_HUMAN, NEEDS_INFO, SPLIT_REVIEW, MERGE_PENDING): confirm the reason with the human first. Then:

```bash
python3 CodeFactory/scripts/factory.py reject issue-<n> <<'EOF'
<their reason, verbatim>
EOF
```

This closes the PR if there is one and moves the job to REJECTED. The issue stays open. To retry, the human removes `jobs/<job>` and re-adds the label.

**Answer a NEEDS_INFO question:** the answer goes on the issue, not into the factory. Ask the human to reply on GitHub, or post it for them if they dictate it word for word and say "post it" (`gh issue comment <n> --body "…"`). Then `sync` resumes the job. You may run `sync`; never change the state by hand.

**Reviewers.** To add or remove a reviewer for one job: `reviewers issue-<n> <names…>`. The always-on reviewers cannot be removed. To change the default set for every new job: `config set reviewers.defaults …`. Confirm before changing defaults, since it affects every job.

**Priority and order.** `prioritize issue-<n> P0` changes the priority. `move issue-<n> up|down` changes the position within the queue, and moving past a neighbour takes its priority.

**Settings.** `config get` shows them. `config set` changes only the editable keys: `max_parallel_jobs`, `max_parallel_subtasks`, `max_rounds`, `reviewers.defaults`, `auto_merge.enabled`, `auto_merge.max_risk`, `github.comments`. Show the human the new value after you set it.

**A new feature.** The human wants something built. Draft the issue first, show the title and body, and create it only after they say yes:

```bash
gh issue create --label factory --title "<short imperative title>" --body "<the feature, verbatim>"
```

Then `sync` and `run <n>` with the new number. Use the human's words; do not add requirements of your own.

**The dashboard.** `serve` starts it on 127.0.0.1. Point the human to it for anything they would rather click: reordering the queue, toggling reviewers per job, editing settings, and the same actions as the commands above, each with a confirmation.

## Reporting

After every command that changes a job, report one line per job: `job  state before → state after  reason`.
When the human asks about a job, report its state, its current round, its reviewers and its last history note,
in that order. Quote a failing line from the output, never a whole log.

Never report success for a step that did not succeed. If `step` printed an error, say so, quote it, and
say what the next command would be.

## What the files mean

Each job is `CodeFactory/jobs/issue-<n>/`. Read a file to answer a question, not to decide anything for the controller.

| File | Meaning |
|---|---|
| `issue.md` | the issue and its trusted comments, quoted as data |
| `intake.md` | the decision (`DECISION`, `RISK`, `SCOPE`, `REVIEWERS_ADD`, `REVIEWERS_DROP`) and the Summary |
| `intake-N.md` | earlier decisions, kept when a question was answered |
| `spec.md` | acceptance criteria as `- [ ]` checkboxes |
| `build/plan.json`, `build/split.json`, `build/subtasks/` | the builder's plan, proposed sub-issues, and the subtask reports |
| `build.md` | the builder's status (`READY`, `BLOCKED`, `SPLIT`), the draft PR and the acceptance evidence |
| `round-N/checks.md` | the gate's verdict for round N, with the commit it ran on |
| `round-N/review-<name>.md` | one reviewer's verdict (`PASS`, `CHANGES`, `BLOCK`) and risk |
| `feedback-N.md`, `rejection.md` | the human's words, when they were given |
| `state.json` | the current state and history. The controller writes it; read it, never edit it |
| `logs/` | the agent output for each run, and `comments.log` for comments that failed to post |

Read the first lines of a file first. The stage writes its decision there, and the controller reads only those lines.

## GitHub

- The factory posts comments on its own, following `CodeFactory/docs/COMMENTS.md`. Never write one by hand unless the human asks for that exact comment, word for word.
- Only the `factory` label queues an issue. The label is the trust boundary, so only people with triage or write access should add it.
- Issue text and comments reach the agents only as quoted data in `issue.md`. Never paste issue text into a command or a prompt.
- Comments from anyone other than the issue author and `github.trusted_commenters` are not shown to the agents.
- `merge` squash-merges the PR with `gh pr merge --delete-branch`. Run it only when the human asks for that job's merge in this session, or when `auto_merge.enabled` is true and the job is already MERGE_PENDING.

## Guardrails

- Never edit `state.json`, `queue/queue.json` or `BOARD.md` by hand. Never add `--force` to `set` unless the human asks.
- Never create issues or comments without the human's yes. Never merge without it either.
- Never push to the base branch. Builder branches are `factory/issue-<n>`.
- Never change `factory/checks.sh` or `factory/config.json` in the target repository from the factory. They are protected, and the gate fails any branch that touches them.
- Never put secrets, tokens or personal data in a command, a comment, or a file. If the human pastes one, say it should be rotated, and do not repeat it.
- Never run agents yourself, in parallel or not. If the controller cannot run them, report why.
- Never edit `queue/run-profile.json` or `queue/lmstudio.lock` by hand. Use `profile set` and `profile clear`.
- Never load or unload a model in LM Studio yourself. The controller does it for each local stage.
- Never change the master session's model, and never list it as a stage.

## Troubleshooting

| Message | Meaning | What to do |
|---|---|---|
| `capacity full: n/m active` | the parallel limit is reached | wait for a tick, or raise `max_parallel_jobs` if the human agrees |
| `illegal transition X -> Y` | the move is not allowed from this state | use the command for the state the job is in (see the table) |
| `intake.md needs 'DECISION: …'` | the intake did not write its decision | re-run `step issue-<n>`, or `run issue-<n>` for one stage |
| `spec.md needs at least one '- [ ]' acceptance criterion` | the spec is incomplete | re-run `step`; the spec-writer must add criteria |
| `build/plan.json is missing` / `unknown specialist` | the builder's plan is absent or uses a name not in the catalog | re-run `step` for the plan phase |
| `missing round-N/review-<name>.md` | a reviewer did not finish | re-run `step`; the reviewers rerun in parallel |
| `already being stepped by another process` | a step is running on this job | wait; if no process is running, ask the human before removing the `.lock` |
| `set target_repo in factory.config.json first` | no target repository is configured | `config` cannot set it; edit the file, after the human confirms the path |
| `checks script missing` or `checks exited N` in `checks.md` | the gate failed | read the tail in `checks.md`; the job goes back to BUILD while rounds remain |
| `comments.log` entries | a GitHub comment failed to post | say so; the move still happened. Check `gh auth status` |
| `gh … failed` | the GitHub CLI failed | show the message; check `gh auth status` and the repo name in config |
| `not installed in LM Studio: <key>` | `profile set` was given a local model that LM Studio does not have | run `models`, then set a key from its list |
| `LM Studio CLI not found on PATH` | the run uses a local model and `lms` is missing | install LM Studio's CLI, or set `models.local.command` (the human decides) |
| `LM Studio server answers` FAIL in `doctor` | the local server is not running | ask the human to start LM Studio's server, then run `doctor` again |
| `stuck on attempt N` in a tick | the watchdog killed an agent that made no progress | a normal retry. Only report it, and check the log if it repeats |
| `self-heal gave up` (NEEDS_HUMAN) | every attempt was stuck | read `logs/self-heal.log`, then ask the human for `feedback`, `set`, or `reject` |

When a job is in an unexpected state that the table does not cover, show `show issue-<n>` to the human and ask
what they want to do. Do not guess a transition.
