#!/usr/bin/env bash
# Checks for a Python project. Uses the project's own tools; nothing here writes files.
set -euo pipefail

python3 -m ruff check .
python3 -m ruff format --check .
python3 -m mypy .
python3 -m pytest -q
