"""Where things live. Tests point these at a temporary copy; nothing else hard-codes a path."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # CodeFactory/
CONFIG = ROOT / "factory.config.json"
INDEX = ROOT / "queue" / "queue.json"
JOBS = ROOT / "jobs"
AGENTS = ROOT / "agents"
BOARD = ROOT / "BOARD.md"
DASHBOARD = ROOT / "dashboard" / "index.html"
TEMPLATES = ROOT / "templates"
ENTRY = ROOT / "scripts" / "factory.py"
