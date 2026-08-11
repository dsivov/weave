"""`python -m weave.devhost` — the dev-host daemon entry point (A1).

The published step for attaching a machine is

    python3 -m weave.devhost --server http://<server>:9800 --workspace team

and until this file existed that command failed with *No module named
weave.devhost.__main__*, because `main()` lived in `daemon.py` behind a
`if __name__ == "__main__"` guard that only fires for
`python -m weave.devhost.daemon`. The guide named the shorter form, which is the
one anybody would write.

**Deliberately thin (R75).** A dev host runs containers; it is not a Weave
server. Importing the daemon here and nothing else keeps the import surface to
`httpx` + the standard library, so a developer machine needs neither the
PostgreSQL nor the Neo4j driver to join a fleet. Anything heavier imported at
this level would be installed on every machine in the fleet to satisfy a code
path none of them run.
"""

from __future__ import annotations

import sys

from weave.devhost.daemon import main

if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
