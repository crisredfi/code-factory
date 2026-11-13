"""CodeFactory controller, split by responsibility.

  paths.py      where files live (tests point these at a temp copy)
  model.py      states, transitions, columns, priorities, risks
  util.py       helpers: json, parsing of KEY: value headers, waves, git and gh wrappers
  config.py     factory.config.json: load, validate, edit
  state.py      per-job state.json, the queue index, transitions, board, per-job lock
  reviewers.py  which reviewers a job runs
  gate.py       worktrees and the deterministic gate (checks.sh)
  decide.py     the gates: intake, spec, build, review decisions
  agents.py     prompts and agent runners (parallel, with dependency waves)
  github.py     every GitHub call (gh CLI)
  lifecycle.py  human moves: merge, split, reject, feedback, priority, queue order, reviewers
  steps.py      run a job's stage and apply its gate
  queue.py      sync from GitHub, start jobs, tick
  report.py     status, show, plan, doctor
  api.py        dashboard server and JSON API
  cli.py        command line

Entry point: scripts/factory.py.
"""
