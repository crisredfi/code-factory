"""Load, validate and edit factory.config.json."""
from __future__ import annotations

from . import paths, util
from .model import RISKS
from .util import FactoryError

# key -> type. Only these can be changed from the CLI or dashboard.
EDITABLE = {
    "max_parallel_jobs": int,
    "max_parallel_subtasks": int,
    "max_rounds": int,
    "reviewers.defaults": list,
    "auto_merge.enabled": bool,
    "auto_merge.max_risk": str,
    "github.comments": bool,
}


def load() -> dict:
    cfg = util.read_json(paths.CONFIG, None)
    if cfg is None:
        raise FactoryError(f"missing config: {paths.CONFIG}")
    return cfg


def save(cfg: dict) -> None:
    util.write_json(paths.CONFIG, cfg)


def set_value(cfg: dict, key: str, value) -> dict:
    """Validate and store one editable setting, then save the file."""
    if key not in EDITABLE:
        raise FactoryError(f"not editable: {key} (editable: {', '.join(EDITABLE)})")
    kind = EDITABLE[key]
    if kind is list:
        value = util.names_in(",".join(value)) if isinstance(value, list) else util.names_in(str(value))
        unknown = [v for v in value if v not in cfg["reviewers"]["catalog"]]
        if unknown:
            raise FactoryError(f"unknown reviewers: {', '.join(unknown)}")
    elif kind is bool:
        value = value in (True, "true", "yes", "on", "1", 1)
    elif kind is int:
        value = int(value)
        if value < 1:
            raise FactoryError(f"{key} must be at least 1")
    elif key == "auto_merge.max_risk" and value not in RISKS:
        raise FactoryError(f"max_risk must be one of {RISKS}")
    head, _, tail = key.partition(".")
    if tail:
        cfg[head][tail] = value
    else:
        cfg[key] = value
    save(cfg)
    return cfg
