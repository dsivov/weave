"""The HTTP surface — the only place HTTP exists in this repository (A2).

The FastAPI app, its routers, configuration, authentication and the mounted MCP
sub-app. Every route is a thin adapter over a service function that the CLI and
the MCP tools call too, so the human and agent surfaces cannot answer the same
question differently (A9).

``__api_version__`` is defined in :mod:`weave_core.version` and re-exported here:
the model connectors stamp it into their ``User-Agent``, and the engine may not
import the product layer.
"""

from __future__ import annotations

import os
from typing import Optional

from weave_core.version import __api_version__ as __api_version__

#: Where Weave keeps its data when nothing says otherwise — **the one
#: definition** (W27).
#:
#: There were five: `weave/server/config.py` said `./rag_storage`, while
#: `weave/cli/{users,_local,migrate,server}.py` each said `./weave_storage`. So
#: an operator who created the first administrator and then started the server
#: without `WEAVE_WORKING_DIR` had an account the server could not see, **and
#: both halves reported success**. It presents as *"I created an admin and
#: cannot log in"*, which is the hardest kind of thing to diagnose because
#: nothing failed.
#:
#: The documented path escaped it only because `weave init` writes
#: `WEAVE_WORKING_DIR` into `weave.env`. Docker, a process manager, or anyone
#: who skipped `init` did not.
#:
#: **It lives here** because this module is what both sides can import: the CLI
#: already imports from `weave.server`, and `weave.server.config` cannot be
#: imported for a constant — it calls `load_dotenv()` at import time, so reading
#: a default from it would load a `.env` from the caller's directory as a side
#: effect.
DEFAULT_WORKING_DIR = "./weave_storage"


#: How many worker processes to start when nothing says otherwise (W26).
#:
#: The default was `weave_core.constants.DEFAULT_WOKERS = 2`, which was wrong
#: three ways at once: the flag's own help said *"default: from env or 1"*, the
#: uvicorn path logged `Forcing workers=1 … (Ignoring workers=2)` on **every**
#: start, and **A7 refuses** two workers on the in-process bus — which is the
#: default bus, because the default storage path is file-based and therefore
#: single-operator. One is the only number consistent with the other defaults.
#:
#: Declared here rather than corrected in `weave_core`: the core is separable
#: (A2), and changing a constant it publishes is its own change with its own
#: blast radius.
DEFAULT_WORKERS = 1


def resolve_working_dir(explicit: Optional[str] = None) -> str:
    """The working directory, resolved the same way by every entry point.

    A shared *function* rather than only a shared constant: the precedence —
    an explicit flag, then `WEAVE_WORKING_DIR`, then the default — is as easy to
    get inconsistently right as the string was, and it is the precedence that
    decides which directory an operator ends up writing to.
    """
    return explicit or os.environ.get("WEAVE_WORKING_DIR") or DEFAULT_WORKING_DIR
