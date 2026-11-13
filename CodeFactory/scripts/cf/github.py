"""Every call to GitHub goes through here, through the gh CLI. Nothing else touches the network."""
from __future__ import annotations

import json

from . import util


def fetch_issues(cfg: dict) -> list[dict]:
    cmd = ["issue", "list", "--state", "open", "--label", cfg["github"]["queue_label"],
           "--limit", "200", "--json", "number,title,body,labels,url,author,comments"]
    if cfg["github"].get("repo"):
        cmd += ["-R", cfg["github"]["repo"]]
    return json.loads(util.gh(*cmd))


def create_issue(cfg: dict, title: str, body: str) -> int:
    url = util.gh("issue", "create", "--title", title, "--body", body,
                  "--label", cfg["github"]["queue_label"]).strip()
    return int(url.rstrip("/").split("/")[-1])


def merge_pr(pr_url: str, method: str) -> None:
    util.gh("pr", "merge", pr_url, f"--{method}", "--delete-branch")


MARKER = "<!-- factory -->"


def post_comment(kind: str, target, body: str) -> None:
    """Post a comment on an issue (target = number) or a PR (target = url). Body already has the marker."""
    if not body.startswith(MARKER):
        raise ValueError("factory comments must start with the marker")
    if kind == "issue":
        cmd = ["issue", "comment", str(target), "--body", body]
    elif kind == "pr":
        cmd = ["pr", "comment", str(target), "--body", body]
    else:
        raise ValueError(f"unknown comment target {kind}")
    util.gh(*cmd)


def close_pr(pr_url: str) -> None:
    util.gh("pr", "close", pr_url)


def merge_command(pr_url: str, method: str) -> list[str]:
    """The command a dry run prints, so a human can see what would run."""
    return ["gh", "pr", "merge", pr_url, f"--{method}", "--delete-branch"]
