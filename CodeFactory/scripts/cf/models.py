"""Which model runs each stage: the run profile, model specs, and loading local models in LM Studio.

A model spec is one of two things:
- a Claude model id listed in `models.claude` in the config, for example `claude-opus-5-5`;
- `local:<model-key>`, a model installed in LM Studio, for example `local:qwen3.6-35b-a3b-splash`.

The run profile (`queue/run-profile.json`) is chosen when a run starts. It maps stages to specs. A stage
that is not in the profile uses the model the config gives it. The master session is not a stage, so
no profile entry can change the model it runs on.

Local runs go through LM Studio's Anthropic-compatible endpoint, so the same `claude -p` runner serves
both kinds of model. Only one local model is loaded at a time: before a local stage runs, the factory
unloads whatever else is loaded, unless the wanted model is already loaded.
"""
from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import paths, util
from .util import FactoryError

LOCAL_PREFIX = "local:"
COMPACT_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"  # read by Claude Code; set only for local children
WINDOW_ENV = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"    # the real window of a model Claude Code does not recognise
STAGES = ["intake", "spec", "build", "specialists", "review"]  # review.<reviewer> is also allowed


def is_local(spec: str) -> bool:
    return spec.startswith(LOCAL_PREFIX)


def local_key(spec: str) -> str:
    return spec[len(LOCAL_PREFIX):]


def load_profile() -> dict:
    return util.read_json(paths.PROFILE, {"stages": {}})


def save_profile(profile: dict) -> None:
    util.write_json(paths.PROFILE, profile)


def clear_profile() -> None:
    if paths.PROFILE.exists():
        paths.PROFILE.unlink()


def resolve(profile: dict, stage: str, default: str) -> str:
    """The spec for one stage: its own entry, then its group (review for review.<name>), then the default."""
    chosen = profile.get("stages", {})
    if stage in chosen:
        return chosen[stage]
    group = stage.split(".", 1)[0]
    return chosen.get(group, default)


def validate_stage(stage: str, cfg: dict) -> None:
    if stage in STAGES:
        return
    if stage.startswith("review.") and stage.split(".", 1)[1] in cfg["reviewers"]["catalog"]:
        return
    names = ", ".join(STAGES + [f"review.{n}" for n in cfg["reviewers"]["catalog"]])
    raise FactoryError(f"unknown stage '{stage}' (stages: {names})")


def validate_model(spec: str, cfg: dict) -> None:
    if is_local(spec):
        if not local_key(spec):
            raise FactoryError("local model needs a key, for example local:qwen3.6-35b-a3b-splash")
        return
    if spec not in cfg["models"]["claude"]:
        raise FactoryError(f"unknown model '{spec}' (Claude: {', '.join(cfg['models']['claude'])}; "
                           "local: local:<key> from `factory.py models`)")


def set_stages(pairs: list[str], cfg: dict) -> dict:
    """Validate `stage=model` pairs and merge them into the saved profile. Local keys must be installed."""
    chosen = dict(load_profile().get("stages", {}))
    local_wanted = []
    for pair in pairs:
        if "=" not in pair:
            raise FactoryError(f"expected stage=model, got '{pair}'")
        stage, spec = pair.split("=", 1)
        stage, spec = stage.strip(), spec.strip()
        validate_stage(stage, cfg)
        validate_model(spec, cfg)
        if is_local(spec):
            local_wanted.append(local_key(spec))
        chosen[stage] = spec
    if local_wanted:
        installed = LMStudio(cfg).installed()
        missing = [k for k in local_wanted if k not in installed]
        if missing:
            raise FactoryError(f"not installed in LM Studio: {', '.join(missing)} (installed: {', '.join(installed)})")
    profile = {"stages": chosen, "updated_at": util.now()}
    save_profile(profile)
    return profile


def table(profile: dict, cfg: dict) -> list[tuple[str, str]]:
    """Every stage with the spec it would run on now (profile first, then config)."""
    defaults = {s: cfg["stages"][s]["model"] for s in ["intake", "spec", "build"]}
    defaults["specialists"] = "per specialist (config)"
    defaults["review"] = "per reviewer (config)"
    rows = []
    for s in ["intake", "spec", "build", "specialists", "review"]:
        rows.append((s, profile.get("stages", {}).get(s) or defaults[s]))
    for name, rv in cfg["reviewers"]["catalog"].items():
        key = f"review.{name}"
        rows.append((key, resolve(profile, key, rv["model"])))
    return rows


class LMStudio:
    """A thin wrapper over the `lms` CLI and the LM Studio server."""

    def __init__(self, cfg: dict):
        c = cfg["models"]["local"]
        self.cmd = c.get("command", "lms")
        self.base = c["base_url"].rstrip("/")
        self.token = c.get("auth_token", "lmstudio")
        self.unload_others = c.get("unload_others", True)
        # Context length the model is loaded with (`lms load -c`). Unset: LM Studio's default.
        self.context_length = c.get("context_length")
        # Claude Code compacts the conversation when it reaches this many tokens. Local children only.
        self.auto_compact_window = c.get("auto_compact_window")

    def _lms(self, *args: str, timeout: int = 120) -> str:
        if not shutil.which(self.cmd):
            raise FactoryError(f"LM Studio CLI not found on PATH: {self.cmd}")
        try:
            res = subprocess.run([self.cmd, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise FactoryError(f"{self.cmd} {' '.join(args)} timed out after {timeout}s") from e
        if res.returncode != 0:
            raise FactoryError(f"{self.cmd} {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")
        return res.stdout

    @staticmethod
    def _json(text: str) -> list:
        # `lms` may print a status line before the JSON, so start reading at the first bracket.
        start = text.find("[")
        if start < 0:
            return []
        try:
            return json.loads(text[start:])
        except ValueError as e:
            raise FactoryError(f"LM Studio returned output that is not JSON: {text[:120]!r}") from e

    def installed(self) -> list[str]:
        return sorted(m["modelKey"] for m in self._json(self._lms("ls", "--json")) if m.get("type") == "llm")

    def loaded(self) -> list[dict]:
        return self._json(self._lms("ps", "--json"))

    def ensure_loaded(self, key: str) -> str:
        """Make `key` the loaded model. Keeps it if it is already loaded; otherwise unloads the rest, then loads it."""
        loaded = self.loaded()
        if any(key in (m.get("modelKey"), m.get("identifier")) for m in loaded):
            return f"{key} already loaded"
        if self.unload_others:
            for m in loaded:
                self._lms("unload", m.get("identifier") or m["modelKey"])
        load = ["load", key, "-y"]
        if self.context_length:
            load += ["-c", str(self.context_length)]
        self._lms(*load, timeout=1800)
        if not any(key in (m.get("modelKey"), m.get("identifier")) for m in self.loaded()):
            raise FactoryError(f"LM Studio did not report {key} as loaded after `load`")
        unloaded = [m.get("identifier") or m["modelKey"] for m in loaded] if self.unload_others else []
        return f"loaded {key}" + (f" (unloaded {', '.join(unloaded)})" if unloaded else "")

    def healthy(self) -> bool:
        """True when the LM Studio server answers its model list."""
        req = urllib.request.Request(f"{self.base}/v1/models", headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def child_env(self, base_env: dict) -> dict:
        """Environment for a `claude -p` child that talks to LM Studio. The real Anthropic key is removed,
        so it is never sent to the local server. The master session's own environment is not changed."""
        env = dict(base_env)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop(COMPACT_ENV, None)
        env.pop(WINDOW_ENV, None)
        env["ANTHROPIC_BASE_URL"] = self.base
        env["ANTHROPIC_AUTH_TOKEN"] = self.token
        # Claude Code does not know a local model id, so it assumes a 1M window unless told the real one.
        if self.context_length:
            env[WINDOW_ENV] = str(self.context_length)
        if self.auto_compact_window:
            env[COMPACT_ENV] = str(self.auto_compact_window)
        return env


@contextmanager
def local_turn(cfg: dict, spec: str, log=print):
    """Hold the LM Studio lock while a local model is loaded and its agent runs. Other local runs wait."""
    lm = LMStudio(cfg)
    paths.LMSTUDIO_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.LMSTUDIO_LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            log(lm.ensure_loaded(local_key(spec)))
            yield lm
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
