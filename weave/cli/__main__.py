"""`python -m weave.cli` — the same entry point as the installed `weave` script,
for a checkout that has not been pip-installed."""

import sys

from weave.cli import main

if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
