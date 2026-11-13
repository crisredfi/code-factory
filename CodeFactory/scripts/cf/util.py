"""Small helpers with no dependency on the rest of the package."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path


class FactoryError(Exception):
    """A gate or precondition failed. Shown to the operator; state is left unchanged."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def field(doc: str, key: str) -> str:
    """Value of a `KEY: value` line (optionally bold or bulleted). '' when absent."""
    m = re.search(rf"^\s*(?:[-*]\s+)?\**{key}\**\s*:\s*\**(.*?)\**\s*$", doc, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def first_word(value: str) -> str:
    parts = value.split()
    return parts[0].strip(".,;:").upper() if parts else ""


def names_in(value: str) -> list[str]:
    return [n.strip().lower() for n in value.split(",") if n.strip() and n.strip().lower() != "none"]


def section(doc: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", doc, re.M | re.S)
    return m.group(1).strip() if m else ""


def waves(items: list[dict]) -> list[list[dict]]:
    """Group items with `id` and `depends_on` into dependency waves (parallel within a wave)."""
    ids = {i["id"] for i in items}
    for i in items:
        missing = set(i.get("depends_on", [])) - ids
        if missing:
            raise FactoryError(f"{i['id']} depends on unknown id(s): {', '.join(sorted(missing))}")
    remaining = {i["id"]: i for i in items}
    done: set[str] = set()
    out: list[list[dict]] = []
    while remaining:
        ready = sorted((i for i in remaining.values() if set(i.get("depends_on", [])) <= done),
                       key=lambda i: i["id"])
        if not ready:
            raise FactoryError("dependency cycle in plan")
        out.append(ready)
        for i in ready:
            done.add(i["id"])
            del remaining[i["id"]]
    return out


def git(cwd, *args, check: bool = True) -> str:
    res = subprocess.run(["git", *map(str, args)], cwd=str(cwd), capture_output=True, text=True)
    if check and res.returncode != 0:
        raise FactoryError(f"git {' '.join(map(str, args))} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout if res.returncode == 0 else ""


def gh(*args) -> str:
    if not shutil.which("gh"):
        raise FactoryError("gh CLI not found on PATH")
    res = subprocess.run(["gh", *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise FactoryError(f"gh {' '.join(args[:2])} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout
