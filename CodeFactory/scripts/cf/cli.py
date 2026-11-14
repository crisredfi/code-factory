"""Command line. Each command is a thin wrapper over a function in the other modules."""
from __future__ import annotations

import argparse
import json
import sys

from . import api, config, lifecycle, models, queue, report, state, steps, agents, reviewers
from .util import FactoryError, names_in


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory.py", description="CodeFactory controller")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, *args):
        s = sub.add_parser(name, help=help_)
        for a in args:
            s.add_argument(a[0], **a[1])
        return s

    job = ("job", {})
    dry = ("--dry-run", {"action": "store_true"})
    add("sync", "pull `factory` issues into the queue", ("--from-file", {}))
    add("status", "show the board")
    add("next", "start queued jobs up to max_parallel_jobs")
    add("tick", "reconcile, start queued jobs, step active jobs", dry)
    add("step", "run the agents for a job's stage, then advance", job, dry)
    add("run", "run agents for a stage without advancing", job, ("--stage", {}), ("--agent", {}), ("--model", {}), dry)
    add("advance", "apply the gate for the current stage", job)
    add("show", "show one job and its next step", job)
    add("plan", "print subtask waves from build/plan.json", job)
    add("prioritize", "set a job priority", job, ("priority", {}))
    add("move", "move a queued job up or down", job, ("direction", {"choices": ["up", "down"]}))
    add("reviewers", "override a job's reviewer set", job, ("names", {"nargs": "*"}), ("--reset", {"action": "store_true"}))
    add("feedback", "send a needs-human job back to the builder (text on stdin)", job)
    add("reject", "reject a parked job (reason on stdin)", job)
    add("split", "approve or cancel a builder's sub-issue split", job, ("decision", {"choices": ["approve", "cancel"]}), dry)
    add("merge", "squash-merge a MERGE_PENDING job's PR", job, dry)
    add("set", "move a job to a state", job, ("state", {}), ("--note", {}), ("--force", {"action": "store_true"}))
    add("config", "read or change editable settings", ("action", {"choices": ["get", "set"]}),
        ("key", {"nargs": "?"}), ("value", {"nargs": "?"}))
    add("models", "list the Claude models and the local models (LM Studio) a run can use")
    add("profile", "show, set or clear which model each stage runs on for this run", ("action", {"choices": ["show", "set", "clear"]}),
        ("pairs", {"nargs": "*", "metavar": "stage=model"}))
    add("serve", "dashboard and JSON API on 127.0.0.1", ("--port", {"type": int, "default": 8765}))
    add("doctor", "check config, agents, gh, runner and the checks script")
    return p


def run(args, cfg: dict) -> None:
    c = args.cmd
    if c == "sync":
        print(queue.sync(cfg, args.from_file))
    elif c == "status":
        print(report.status(cfg))
    elif c == "next":
        print("\n".join(queue.next_jobs(cfg)))
    elif c == "tick":
        queue.tick(cfg, args.dry_run)
    elif c == "step":
        steps.step_job(args.job, cfg, args.dry_run)
    elif c == "run":
        st = state.load_state(args.job)
        from_state = {"INTAKE": ["intake"], "SPEC": ["spec"], "BUILD": ["build"],
                      "REVIEW": [f"review.{n}" for n in reviewers.review_names(st, cfg)]}.get(st["state"], [])
        keys = [args.stage] if args.stage else from_state
        if not keys:
            raise FactoryError(f"nothing to run in {st['state']}")
        items = [it for k in keys for it in agents.stage_items(k, cfg, st, phase=(st.get("phase") or "plan") if k == "build" else None)]
        if args.agent:
            items[0]["agent"] = args.agent
        for it in items:
            if args.model:
                it["model"] = args.model
                it["pinned"] = True  # an explicit --model beats the run profile
        agents.execute(args.job, items, cfg, args.dry_run)
    elif c == "advance":
        to, note = steps.apply(args.job, cfg)
        print(f"{args.job}: -> {to} ({note})")
    elif c == "show":
        print(report.show(args.job))
    elif c == "plan":
        print(report.plan(args.job, cfg))
    elif c == "prioritize":
        lifecycle.set_priority(args.job, args.priority)
        print(f"{args.job} priority {args.priority}")
    elif c == "move":
        print(lifecycle.move_job(args.job, args.direction))
    elif c == "reviewers":
        names = None if args.reset else args.names
        print("reviewers: " + ", ".join(lifecycle.set_reviewers(args.job, cfg, names)))
    elif c == "feedback":
        lifecycle.feedback_job(state.load_state(args.job), cfg, sys.stdin.read())
        print(f"{args.job}: back to build")
    elif c == "reject":
        lifecycle.reject_job(state.load_state(args.job), cfg, sys.stdin.read())
        print(f"{args.job}: rejected")
    elif c == "split":
        st = state.load_state(args.job)
        if args.decision == "cancel":
            lifecycle.cancel_split(st)
            print(f"{args.job}: split cancelled")
        else:
            print(lifecycle.approve_split(st, cfg, args.dry_run))
    elif c == "merge":
        print(lifecycle.merge_job(state.load_state(args.job), cfg, args.dry_run))
    elif c == "set":
        state.set_state(state.load_state(args.job), args.state.upper(), args.note or "manual", force=args.force)
        print(f"{args.job}: -> {args.state.upper()}")
    elif c == "config":
        if args.action == "get":
            shown = {k: cfg[k] for k in ["max_parallel_jobs", "max_parallel_subtasks", "max_rounds"]}
            shown |= {"reviewers.defaults": cfg["reviewers"]["defaults"], "auto_merge": cfg["auto_merge"]}
            print(json.dumps(shown, indent=2))
        else:
            if not args.key or args.value is None:
                raise FactoryError("usage: config set <key> <value>")
            val = names_in(args.value) if args.key == "reviewers.defaults" else args.value
            config.set_value(cfg, args.key, val)
            print(f"{args.key} saved")
    elif c == "models":
        print(report.models_list(cfg))
    elif c == "profile":
        if args.action == "show":
            print(report.profile(cfg))
        elif args.action == "set":
            if not args.pairs:
                raise FactoryError("usage: profile set <stage>=<model> [<stage>=<model> ...]")
            models.set_stages(args.pairs, cfg)
            print(report.profile(cfg))
        else:
            models.clear_profile()
            print("profile cleared: every stage uses the models in the config")
    elif c == "serve":
        api.serve(args.port)
    elif c == "doctor":
        print(report.doctor(cfg))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args, config.load())
    except FactoryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
