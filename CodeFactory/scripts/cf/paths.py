"""Where things live. Tests point these at a temporary copy; nothing else hard-codes a path."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # CodeFactory/
CONFIG = ROOT / "factory.config.json"
INDEX = ROOT / "queue" / "queue.json"
PROFILE = ROOT / "queue" / "run-profile.json"  # the models chosen for the current run
LMSTUDIO_LOCK = ROOT / "queue" / "lmstudio.lock"  # one local model is loaded at a time
JOBS = ROOT / "jobs"
AGENTS = ROOT / "agents"
BOARD = ROOT / "BOARD.md"
DASHBOARD = ROOT / "dashboard" / "index.html"
TEMPLATES = ROOT / "templates"
ENTRY = ROOT / "scripts" / "factory.py"
