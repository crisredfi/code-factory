# Install CodeFactory

This guide takes a fresh checkout of the factory to a working queue, with one target repository
building under it. It takes about 20 minutes, most of it setting up the checks script.

- [1. Requirements](#1-requirements)
- [2. Place the factory](#2-place-the-factory)
- [3. Link agents and the skill into Claude Code](#3-link-agents-and-the-skill-into-claude-code)
- [4. Pick a target repository](#4-pick-a-target-repository)
- [5. Install the checks script](#5-install-the-checks-script)
- [6. Configure the factory](#6-configure-the-factory)
- [7. Check the install](#7-check-the-install)
- [8. First run](#8-first-run)
- [9. Run it on a schedule (optional)](#9-run-it-on-a-schedule-optional)
- [10. Troubleshooting](#10-troubleshooting)
- [11. Uninstall](#11-uninstall)

## 1. Requirements

| Tool | Why | Check |
|---|---|---|
| Python 3.10 or newer | the controller (standard library only) | `python3 --version` |
| git 2.30 or newer | worktrees, one per job | `git --version` |
| GitHub CLI (`gh`), logged in | queue, PRs, sub-issues, merges | `gh auth status` |
| Claude Code CLI (`claude`) | runs the agents in print mode | `claude --version` |
| The target repo's own toolchain | the checks script runs it (Xcode, Node, Python, …) | see step 5 |

The target repository needs an `origin` remote and a base branch (usually `main`).

## 2. Place the factory

The factory lives in `CodeFactory/` at the root of your workspace. Nothing in it needs to be installed
globally. Run every command below from the workspace root, the folder that contains `CodeFactory/`.

```bash
cd /path/to/workspace
python3 CodeFactory/scripts/factory.py --help
```

You should see the list of commands. If you do not, check the Python version from step 1.

## 3. Link agents and the skill into Claude Code

Claude Code finds agents and skills in `.claude/` at the workspace root. Link them there, so the
factory's files stay in one place:

```bash
mkdir -p .claude/skills .claude/agents
ln -sfn ../../CodeFactory/skills/code-factory .claude/skills/code-factory
for f in CodeFactory/agents/*.md; do ln -sf ../../"$f" .claude/agents/"$(basename "$f")"; done
```

Check: `ls -la .claude/agents` shows a link for each agent, and `.claude/skills/code-factory` points at
the skill. Start a new Claude Code session in the workspace and the `code-factory` skill should be listed.

## 4. Pick a target repository

The target is the repository the factory builds in. It is separate from `CodeFactory/`. Use an existing
checkout you can fetch and push from. The factory will create one worktree per job, under
`<target>/.worktrees/`, so keep that folder out of your normal work:

```bash
echo ".worktrees/" >> /path/to/target/.git/info/exclude
```

Write down the absolute path. You will need it in step 6.

## 5. Install the checks script

The gate runs `factory/checks.sh` in the target repository before any reviewer sees a branch. It is the
only thing that decides whether a build passes, so make it strict and fast.

Pick the template that matches the target, copy it into the target, and fill it in:

| Target | Template | Then |
|---|---|---|
| Xcode project (iOS, macOS) | `CodeFactory/templates/checks/ios.sh` | set `FACTORY_SCHEME`, and `FACTORY_WORKSPACE` if you have one |
| Node / TypeScript | `CodeFactory/templates/checks/node.sh` | make the script names match `package.json` |
| Python | `CodeFactory/templates/checks/python.sh` | make sure `ruff`, `mypy` and `pytest` are installed |
| Anything else | `CodeFactory/templates/checks/generic.sh` | replace the placeholder with the repo's real commands |

```bash
TARGET=/path/to/target
mkdir -p "$TARGET/factory"
cp CodeFactory/templates/checks/ios.sh "$TARGET/factory/checks.sh"   # or node.sh, python.sh, generic.sh
chmod +x "$TARGET/factory/checks.sh"
```

Then commit it to the target's base branch. The factory treats `factory/checks.sh` and
`factory/config.json` in the target as protected: a branch that changes them fails the gate. Commit
the script on the base branch yourself, not through the factory.

The script must follow these rules:
- exit 0 only when the branch is fit for review;
- be non-interactive and deterministic: no watch modes, no prompts;
- never write files: no `--fix`, no formatters in write mode;
- run in under about 20 minutes (the factory's default timeout is 30).

`doctor` (step 7) refuses a script that still contains the word `PLACEHOLDER`.

## 6. Configure the factory

Edit `CodeFactory/factory.config.json`. These are the settings to change first:

| Key | What to set |
|---|---|
| `target_repo` | absolute path of the target from step 4 |
| `github.repo` | `owner/name` of the target on GitHub (leave empty to use the target's `origin`) |
| `github.base_branch` | the branch PRs go to, usually `main` |
| `max_parallel_jobs` | how many jobs run at once (default 2) |
| `reviewers.defaults` | the reviewers every job runs (default architecture, code, security) |
| `auto_merge.enabled` | leave `false` until you trust the reviewers |
| `github.comments` | `true` posts progress comments on issues and PRs, as described in `docs/COMMENTS.md`. Set `false` to stay silent |

You can change the editable settings from the command line too:

```bash
python3 CodeFactory/scripts/factory.py config get
python3 CodeFactory/scripts/factory.py config set max_parallel_jobs 3
python3 CodeFactory/scripts/factory.py config set reviewers.defaults code,security
```

Settings that are not in that list (paths, the runner, the agent catalog) are edited in the file.

## 7. Check the install

```bash
python3 CodeFactory/scripts/factory.py doctor
```

Every line should say `ok`. Common failures:

| Line | Fix |
|---|---|
| `FAIL gh on PATH` | install `gh` and run `gh auth login` |
| `FAIL runner on PATH` | install the Claude Code CLI, or change `runner.command` |
| `FAIL target_repo set` | step 6 |
| `FAIL checks script present` | step 5 |
| `FAIL checks script is not the placeholder` | finish the checks script |

Run the factory's own tests too. They do not need GitHub or Claude, and they create a temporary
repository to exercise the gate:

```bash
python3 -m unittest discover -s CodeFactory/tests -v
```

## 8. First run

1. Label one small issue in the target repo with `factory`. Use a real but low-risk task.
2. Pull it into the queue:
   ```bash
   python3 CodeFactory/scripts/factory.py sync
   ```
3. Open the dashboard in a second terminal and leave it running:
   ```bash
   python3 CodeFactory/scripts/factory.py serve --port 8765
   ```
   Then open http://127.0.0.1:8765/.
4. Start the first tick from the dashboard's **Tick** button, or from the terminal:
   ```bash
   python3 CodeFactory/scripts/factory.py tick
   ```
   A tick starts queued jobs, then runs the active stage of each job. Run it again to move a job on.
   A job that needs you says so on its card.

Until you trust the reviewers, keep `auto_merge.enabled` false. Merge the first few PRs by hand, and
read their reviews first.

## 9. Run it on a schedule (optional)

To tick every 15 minutes on macOS, create a launchd agent. Replace the two paths:

```xml
<!-- ~/Library/LaunchAgents/com.codefactory.tick.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.codefactory.tick</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/workspace/CodeFactory/scripts/factory.py</string>
    <string>tick</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/workspace</string>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>/tmp/codefactory-tick.log</string>
  <key>StandardErrorPath</key><string>/tmp/codefactory-tick.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.codefactory.tick.plist
```

Unload it with `launchctl unload` on the same file. Keep the dashboard as a separate process, started by hand.

## 10. Troubleshooting

| Symptom | Where to look |
|---|---|
| A job is stuck in BUILD | `factory.py show <job>`, then `jobs/<job>/logs/` for the agent output |
| The gate fails on every round | `jobs/<job>/round-N/checks.md` lists the reason and the tail of the output |
| `already being stepped by another process` | another tick or step is running on that job. Wait, or check for a stale `jobs/<job>/.lock` |
| A PR was not created | the builder's log, and `gh auth status` |
| The dashboard shows `offline` | the `serve` process stopped, or the port is in use. Try another `--port` |

## 11. Uninstall

Remove the links, then the folder. Remove the worktrees and branches the factory created in the target
first, so the target stays clean:

```bash
git -C /path/to/target worktree list
git -C /path/to/target worktree remove --force /path/to/target/.worktrees/<job>
rm -f .claude/skills/code-factory
rm -f .claude/agents/{intake,spec-writer,builder,reviewer-*,ios-engineer,backend-engineer,os-engineer,test-engineer}.md
rm -rf CodeFactory
```
