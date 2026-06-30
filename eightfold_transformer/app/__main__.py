"""Module execution entry point: ``python -m eightfold_transformer.app``.

Delegates to the CLI defined in :mod:`eightfold_transformer.app.cli`, exiting
with the process exit code it returns.
"""

from __future__ import annotations

from eightfold_transformer.app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
