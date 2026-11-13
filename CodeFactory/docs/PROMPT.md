# CodeFactory — the brief (v1, refined)

This is the source brief the factory is built from. It is the original request, plus the
additions made after reviewing how other people build agent factories (see
[Research notes](#research-notes)). Decisions made on top of the request are marked **[decided]**;
open points are marked **[open]**.

## 1. Goal

A factory that turns GitHub issues into merged (or human-gated) pull requests through a fixed,
inspectable pipeline of agents. Every task is tracked in a folder, so at any moment we know which
job is where, what each agent said, and what happens next.

## 2. Pipeline

```
GitHub issue (label: factory)
   │  sync
   ▼
QUEUE (priority P0–P3, FIFO inside a priority)  ── max_parallel_jobs gate
   │  next
   ▼
INTAKE       intake agent        → intake.md   (DECISION: ACCEPT | NEEDS-INFO | REJECT, RISK, SCOPE)
   │ ACCEPT (risk ≠ HIGH)                 ├─ NEEDS-INFO → NEEDS_INFO (waits for the issue author)
   ▼                                      └─ REJECT     → REJECTED
SPEC         spec-writer agent   → spec.md     (testable `- [ ]` acceptance criteria)
   ▼
BUILD        builder agent       → build.md    (plan → subtasks → integrate; TDD; draft PR)
   │  └─ specialists run the subtasks in waves (iOS / backend / OS / test engineers …)
   ▼
REVIEW       reviewer-architecture, reviewer-code, reviewer-security   (+ ui, ux only when requested)
   │  CHANGES_REQUESTED → back to BUILD (builder resumed with the review files)
   │  BLOCK or HIGH risk → NEEDS_HUMAN
   ▼
MERGE_PENDING  all PASS, tests PASS, risk ≤ auto_merge.max_risk  → waiting for merge
   │  merge (only if auto_merge.enabled; otherwise a human merges)
   ▼
COMPLETE

Side states: NEEDS_HUMAN (human input required), FAILED (crashed / unrecoverable), REJECTED, NEEDS_INFO.
```

## 3. Requirements (from the original request)

1. **Queue from GitHub.** Issues labelled `factory` enter the queue. Priority comes from a
   `priority:P0..P3` label, and can be changed locally with an arrow-style command
   (`prioritize`). Default priority: P2.
2. **Parallelism cap.** `max_parallel_jobs` (default 2) limits how many jobs are in an active
   stage (INTAKE, SPEC, BUILD, REVIEW) at once.
3. **Intake agent** (dedicated model). Decides ACCEPT / NEEDS-INFO / REJECT, rates risk and
   surfaces scope. Writes `intake.md`.
4. **Spec-writer** (selectable agent/model). Writes `spec.md` with testable acceptance criteria.
5. **Builder.** Decides whether the job needs sub-jobs, deploys specialists (iOS, backend, OS,
   test …), gathers their output, implements on `factory/<job-id>`, follows TDD (failing tests
   first), is strict about platform norms, commits, pushes, opens a **draft** PR, writes
   `build.md`. Resumed for every new round (review feedback or failing tests).
6. **Build verdict.** Unit tests decide whether the build is finished. Harness constraints and
   acceptance criteria must pass.
7. **Review.** Architecture, code and security reviewers always run. UI and UX reviewers run only
   when the issue carries an explicit special-request label.
8. **Outcome.** All green + low risk → MERGE_PENDING (auto-merge possible). Otherwise →
   NEEDS_HUMAN. COMPLETE is the final column.
9. **Folder + memory.** Everything lives in `CodeFactory/`. Each job has its own folder with a
   `state.json` (state, history, rounds, risk, PR) — this is the memory. `BOARD.md` is the view.

## 4. Additions after research **[decided]**

These come from comparing with public agent-factory designs (Warp's software-factory guide,
Vercel's Foreman, autocode, SWE-AF, LeadDev/ZenML write-ups on risk-gated merge):

- **Labels as state machine, but the controller is the source of truth.** The local
  `jobs/<id>/state.json` decides; GitHub stage labels are an optional mirror
  (`github.sync_stage_labels`, off by default).
- **Gates are code, not prose.** `factory.py advance` reads `DECISION`, `SPEC` checkboxes,
  `TESTS`, `VERDICT`, `RISK` lines and applies the transition. An agent cannot talk its way past a
  gate.
- **Hand-off through files, not return values.** Each stage writes a markdown file that the next
  stage reads. Makes runs resumable and auditable.
- **Subagents do not nest.** The builder *plans* sub-jobs into `build/plan.json`; the controller
  (`run --subtasks`) launches the specialists in dependency waves. The builder then integrates
  their output in a second pass.
- **Risk-gated auto-merge.** Auto-merge is only eligible at `LOW` risk, and only when
  `auto_merge.enabled` is true. Default is **off**, because the global rule is "never merge without
  a live explicit instruction covering that unit of work".
- **Retry budgets.** `max_build_rounds` and `max_review_rounds`. When exhausted, the job goes to
  NEEDS_HUMAN instead of looping forever.
- **Deferred-debt section.** The PR body (and `build.md`) must list what was deliberately left out,
  so reviewers see it.
- **Doctor command.** Checks config, agent files, `gh` and the runner before a batch starts.

## 5. Open points **[open]**

1. Target repo: which repository the builder works in (`target_repo` in config). Currently empty.
2. Base branch for factory PRs (`main` by default). The batch-feature-branch flow from the global
   rules is not automated yet; it would be a config value.
3. Which runner executes agents. v1 uses the `claude` CLI in print mode (`claude -p --model …`).
   Other runners (Codex, etc.) are a config change plus an adapter, not implemented.
4. `--permission-mode acceptEdits` is the default runner flag so agents can write files
   unattended. Review this before running on a real repo.
5. Whether NEEDS_INFO should auto-poll the issue for an answer. v1: a human moves it back with
   `set <job> INTAKE`.

## Research notes

- Label-driven pipelines with a `factory`/`autocode`-style tag and separate spec, build and review
  agents are the common shape (Warp, Vercel Foreman, autocode, owainlewis/factory).
- Draft PRs plus an architect/security reviewer pair, with the PRD and deferred debt in the PR body,
  is how SWE-AF presents work to humans.
- Auto-merge is the contested point. The common middle ground is to gate it on risk
  classification and keep a human on anything above low risk (LeadDev, ZenML).
- Community Claude Code write-ups note that subagents cannot spawn other subagents, so multi-step
  orchestration has to be chained from the outside. That is why the controller runs subtasks.

Sources (accessed while drafting):
- [autocode](https://github.com/ajsai47/autocode)
- [owainlewis/factory](https://github.com/owainlewis/factory)
- [Warp: build a software factory](https://docs.warp.dev/guides/agent-workflows/set-up-a-software-factory)
- [Vercel: eve software factory](https://vercel.com/kb/guide/eve-software-factory.md)
- [SWE-AF architecture](https://cdn.jsdelivr.net/gh/agent-field/swe-af@main/docs/ARCHITECTURE.md)
- [LeadDev: engineer's guide to a software factory](https://leaddev.com/software-quality/the-engineers-guide-to-building-a-software-factory)
- [ZenML: building a software factory with AI agents](https://www.zenml.io/llmops-database/building-a-software-factory-with-ai-agents-and-automation-loops)
- [DEV: label-driven agentic workflows](https://dev.to/serifcolakel/label-driven-agentic-workflows-building-autonomous-software-pipelines-without-a-workflow-engine-16d1)

## v2 changes (second iteration, from review of the reference factory and your feedback)

- **Reviewers are chosen per job.** `reviewers.defaults` is the baseline you edit. The intake adds
  (`REVIEWERS_ADD`) or drops (`REVIEWERS_DROP`) reviewers for the change, within the catalog. `always`
  reviewers cannot be dropped. A human can override a single job from the dashboard.
- **The builder chooses the agents.** It writes `build/plan.json` with mode `single`, `subtasks` or
  `split`. Specialists must come from `agents.catalog`; the controller refuses anything else.
- **The builder can split work into sub-issues.** Mode `split` writes `build/split.json`. The job waits in
  SPLIT_REVIEW. A human approves the drafts, and only then are sub-issues created, labelled `factory` and
  linked with `Parent: #n` and `Depends on: #n`. The parent completes when its children do. This follows the
  rule that GitHub issues are drafted and shown before they are created.
- **Gate is deterministic.** `checks.sh` from the base checkout runs in the job's worktree. Protected paths
  cannot be changed by a branch. The builder's own "tests pass" claim is not trusted.
- **Editable dashboard.** Served by `factory.py serve`. Queue reordering with arrows, priorities, reviewer
  chips per job, settings, feedback, reject, merge, split approval and manual state moves, all with
  confirmations. It is a single page with no build step.
- **Rounds are one budget.** `max_rounds` covers gate and review loops. Human feedback starts a new budget.

Open for the next iteration: cost and token tracking per job, syncing the branch with the base before merge,
runners other than `claude`, and GitHub stage labels.
