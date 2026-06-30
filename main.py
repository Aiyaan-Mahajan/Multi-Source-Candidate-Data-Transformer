#!/usr/bin/env python3
"""Top-level entry point shim for the Multi-Source Candidate Data Transformer.

Lets the documented command work from the repository root::

    python main.py --csv data/candidates.csv --resume data/resume.txt \
        --config configs/default.json

This is a thin shim: it ensures the repo root is importable (so the
``eightfold_transformer`` package resolves when run as a script) and delegates to
:func:`eightfold_transformer.app.cli.main`. Running ``python -m
eightfold_transformer.app ...`` works equivalently.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When invoked as ``python main.py``, the repo root (this file's directory) is
# already on sys.path[0]; add it defensively so the package import resolves even
# if the script is launched from elsewhere.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eightfold_transformer.app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
