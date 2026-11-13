#!/usr/bin/env python3
"""CodeFactory entry point. The logic lives in the cf/ package; see cf/__init__.py for the map."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cf.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
