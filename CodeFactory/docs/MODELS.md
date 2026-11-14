# Models per run, local models, and self-healing

This page is the design for three things: choosing a model for each stage when a run starts, running a stage on
a local model in LM Studio, and noticing and recovering from a stuck agent. The code is in
`scripts/cf/models.py` (profiles and LM Studio), `scripts/cf/watchdog.py` (stuck detection), and
`scripts/cf/agents.py` (where they meet). The operator's walkthrough is in the
[skill](../skills/code-factory/SKILL.md).

## 1. The master session is not a stage

The session that starts the factory runs on the model the human selected for it. That model orchestrates the
run. The factory never changes it. This is enforced in the code: a run profile only accepts names from the
stage list (`intake`, `spec`, `build`, `specialists`, `review`, `review.<reviewer>`), and the master is not in
that list. A test checks that `master` is refused.

## 2. Model specs

A stage runs on one of two kinds of spec:

| Spec | Meaning | Where it is listed |
|---|---|---|
| `claude-opus-5-5` (any id) | a Claude model, run with `claude -p --model <id>` | `models.claude` in the config |
| `local:<key>` | a model installed in LM Studio, run through its Anthropic-compatible endpoint | `lms ls` (`factory.py models`) |

Local runs use LM Studio's Anthropic-compatible endpoint, documented at
[lmstudio.ai/docs/integrations/claude-code](https://lmstudio.ai/docs/integrations/claude-code) and
[lmstudio.ai/docs/developer/anthropic-compat](https://lmstudio.ai/docs/developer/anthropic-compat). The child
process gets `ANTHROPIC_BASE_URL` (the server root, `http://localhost:1234`) and `ANTHROPIC_AUTH_TOKEN`. The real
`ANTHROPIC_API_KEY` is removed from the child's environment, so it is never sent to the local server. The
master's environment is not changed.

## 3. The run profile

`factory.py profile set stage=model …` writes `queue/run-profile.json`. The file is local to the machine and
ignored by git. Resolution for each stage is:

1. the profile's entry for the stage (`review.code`);
2. the profile's entry for its group (`review` covers every reviewer);
3. the model in the config for that stage or reviewer.

An explicit `run <job> --model` beats the profile. `profile clear` removes the file. `profile set` checks that a
local model is installed before it writes anything.

## 4. Loading local models

Before a local stage runs, `models.local_turn` takes the LM Studio lock (`queue/lmstudio.lock`) and calls
`LMStudio.ensure_loaded`:

- if the wanted model is already loaded, nothing is unloaded or loaded;
- otherwise every other loaded model is unloaded (`lms unload <identifier>`), and then the wanted one is loaded
  (`lms load <key> -y`);
- after the load, the controller checks that LM Studio reports the model as loaded, and refuses the stage if not.

`models.local.unload_others` in the config can be set to `false` to keep other
loaded models. The default follows the rule the run was designed around: one model at a time.

The lock is held for the whole local run, so local stages run one at a time. This is the consequence of one
loaded model: a swap cannot happen under a running agent. Claude stages do not take the lock, so they run in
parallel with a local stage. A Claude stage does not unload the local model.

## 5. Self-healing: is the agent really stuck?

The watchdog (`watchdog.run_watched`) polls each agent every `self_heal.poll_s` seconds and asks one question:
is this run making progress? Three signals count as progress:

- the CPU time of the agent's whole process tree (a test run or a build inside the agent is work);
- the git state of its worktree (HEAD and `git status`);
- the files in its job folder (outputs written, round files).

The decision (`watchdog.Watch.check`):

| Condition | Verdict |
|---|---|
| any signal moved since the last poll | ACTIVE |
| silent for less than the limit (`stuck_after_s`, or `local_stuck_after_s` for a local model) | ACTIVE |
| silent past the limit, first time | SUSPECT, check again |
| still silent after `confirm_s` more | STUCK: kill the process group and retry |
| running longer than `max_run_s`, whatever it does | STUCK |
| local model, silent past its limit, and the LM Studio server does not answer | STUCK at once |

The two-step check (SUSPECT, then STUCK) is there so one quiet poll does not kill a working agent.

**What it cannot see.** A local model does its generation inside LM Studio, not in the agent's process. The
CPU signal of the agent's tree is therefore quiet while a local model is thinking. For that reason local runs
have a longer limit (`local_stuck_after_s`, 30 minutes by default), and the server health check is used only
when the run is already silent. If local runs are killed too early, raise that limit first.

**Recovery.** A STUCK run is killed and retried, up to `self_heal.max_retries` times (2 by default), on
`self_heal.fallback_model` if one is set. Every stuck attempt goes to `jobs/<id>/logs/self-heal.log`. When every
attempt is stuck, the stage raises `StuckAgent`, and the job moves to NEEDS_HUMAN with the note
`self-heal gave up: …`. A non-zero exit is not treated as stuck. It fails the stage at once, as before.

## 6. Settings

| Key | Default | Meaning |
|---|---|---|
| `models.claude` | the four Claude models | the Claude ids a profile may name |
| `models.local.command` | `lms` | the LM Studio CLI |
| `models.local.base_url` | `http://localhost:1234` | the LM Studio server |
| `models.local.unload_others` | `true` | unload the other loaded models before loading a local one |
| `self_heal.enabled` | `true` | watch agents and retry stuck ones |
| `self_heal.poll_s` | `15` | seconds between checks |
| `self_heal.confirm_s` | `60` | how long a silence must last after SUSPECT before STUCK |
| `self_heal.stuck_after_s` | `900` | silence before a Claude run is suspect |
| `self_heal.local_stuck_after_s` | `1800` | silence before a local run is suspect |
| `self_heal.max_run_s` | `5400` | longest a single run may take |
| `self_heal.max_retries` | `2` | retries after a stuck run |
| `self_heal.fallback_model` | `""` | model used on a retry (empty: same model) |

`self_heal.enabled` and `self_heal.stuck_after_s` can be changed with `config set`.

## 7. Limits, stated plainly

- Stuck detection is a set of observable signals, not a judgment about the agent's reasoning. A long silent
  generation with no file writes and no CPU looks the same as a hang, until the limit passes.
- The LM Studio side of a local run is observed only through its health endpoint and the model's load state.
  The factory cannot see a generation in progress.
- The lock serialises local stages. Two local models cannot be used at the same time on one LM Studio instance.
- `lms ps --json` is read for the loaded model's `modelKey` or `identifier`. The field names were checked against
  an empty list, not against a loaded model, so the first real load is the test of that parsing.
