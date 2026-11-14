# CodeFactory

**GitHub issues in, reviewed pull requests out.**

CodeFactory is a queue-driven software factory. You label a GitHub issue `factory`. It goes through
intake, a spec, a build with specialist agents, a deterministic gate, parallel reviewers, and then
either waits for a merge or asks a human. Every step writes a file you can read, and every move is
recorded in the job's own folder.

> **Status:** version 2. The controller, gates, dashboard, comments, skill, per-run model choice (Claude or local LM Studio)
> and self-healing are built and unit-tested (93 tests). It has not yet run end to end against a real target repository. Expect rough edges.

## How a job moves

```
issue (label: factory)
  └─ sync ─▶ QUEUED ─tick─▶ INTAKE ─ACCEPT─▶ SPEC ─criteria─▶ BUILD ─gate PASS─▶ REVIEW ─all PASS─▶ MERGE_PENDING ─merge─▶ COMPLETE
                 │             │                              ▲ │  ▲                  │
                 │             ├─NEEDS-INFO─▶ NEEDS_INFO ─────┘ │  └─ gate FAIL or CHANGES (rounds left)
                 │             │      (author answers on the issue; sync resumes)
                 │             └─REJECT─▶ REJECTED
                 │                                 └─ split ─▶ SPLIT_REVIEW ─approve─▶ WAITING_CHILDREN ─▶ COMPLETE
                 └─ BLOCK, HIGH risk, rounds used up, blocked builder ─▶ NEEDS_HUMAN
```

- **Intake** decides whether the issue is worth building, at what scope and risk, and which reviewers it needs.
- **Spec** turns the accepted issue into acceptance criteria as checkboxes.
- **Build** plans the work, chooses specialists (iOS, backend, OS, tests) from a catalog, or proposes a split into sub-issues for a human to approve. Then it implements on its own branch, with tests first, and opens a draft PR.
- **Gate** is code, not an agent. It runs the target repo's own `factory/checks.sh` in the job's worktree, and it refuses any branch that touches protected files.
- **Review** runs the reviewers for that job in parallel: architecture, code and security by default, with UI and UX on request. Any blocking finding sends the job to a human.
- **Merge** is never automatic unless you turn `auto_merge` on, and then only for changes at or below the risk level you set.

## What you get

- **A controller** (`CodeFactory/scripts/factory.py`) that owns all state changes. Agents write files; the controller checks them against gates. Nothing advances on an agent's word alone.
- **A dashboard** you can edit: a kanban of jobs, a queue you can reorder, reviewer toggles per job, settings, and every human action behind a confirmation. It runs on your machine.
- **GitHub comments** posted automatically at the moments people need to know: accepted, needs information, needs a human, ready to merge, merged, sub-issues created. See [docs/COMMENTS.md](CodeFactory/docs/COMMENTS.md).
- **Questions and answers.** A job waiting for information resumes when the author answers on the issue. Earlier decisions are kept, so the next pass does not ask the same thing again.
- **Worked examples** of every file a job writes, which are checked by the tests against the real parsers. See [examples/](CodeFactory/examples/README.md).
- **A Claude Code skill** that runs the factory for you: `/factory run`, `/factory run <n>`, and free-form questions, feedback, rejections and new features. See [the skill](CodeFactory/skills/code-factory/SKILL.md).
- **Models per run.** `/factory run` asks which model each stage uses: Claude, or a local model in LM Studio. The factory loads and unloads local models as the stages need them. See [docs/MODELS.md](CodeFactory/docs/MODELS.md).
- **Self-healing.** An agent that stops making progress is noticed, killed and retried. A job that stays stuck goes to a human. See [docs/MODELS.md](CodeFactory/docs/MODELS.md#5-self-healing-is-the-agent-really-stuck).

## Quick start

You need Python 3.10 or newer, git, the GitHub CLI (`gh`, logged in), and the Claude Code CLI.
The full procedure, including setting up a target repo and its checks script, is in
**[CodeFactory/INSTALL.md](CodeFactory/INSTALL.md)**.

```bash
# from the repository root
python3 CodeFactory/scripts/factory.py doctor            # check the install
python3 CodeFactory/scripts/factory.py sync              # pull issues labelled factory
python3 CodeFactory/scripts/factory.py serve --port 8765 # dashboard at http://127.0.0.1:8765/
python3 CodeFactory/scripts/factory.py tick              # start queued jobs and move active ones on
```

Run the tests with no GitHub or Claude access needed:

```bash
python3 -m unittest discover -s CodeFactory/tests -v
```

## Layout

| Path | What it is |
|---|---|
| `CodeFactory/scripts/cf/` | the controller, one job per module |
| `CodeFactory/agents/` | one markdown file per agent |
| `CodeFactory/dashboard/` | the local dashboard |
| `CodeFactory/skills/code-factory/` | the Claude Code skill |
| `CodeFactory/docs/` | states, comments, the design brief |
| `CodeFactory/templates/checks/` | checks scripts for generic, iOS, Node and Python targets |
| `CodeFactory/examples/` | worked job folders and example comments |
| `CodeFactory/tests/` | unit, git-gate, API, examples and skill tests |
| `.claude/` | links that make the agents and the skill visible to Claude Code |

## Safety

- The `factory` label is the trust boundary. Only people with triage or write access can add it.
- Issue text reaches the agents only as quoted data, and comments from strangers are left out.
- The gate fails any branch that changes the checks script or the factory's config in the target repo.
- The dashboard listens on `127.0.0.1` only.
- Sub-issues are created only after a human approves the split. Merges need an explicit request, or `auto_merge` turned on.

## Acknowledgements

The design draws on the [software factory](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2)
by zazencodes, which shaped the pipeline, the gate and the dashboard. This version is a separate implementation
with its own controller, and it adds per-job reviewers, specialist subtasks, sub-issue splits, an editable dashboard
and GitHub comments.
