"""Weave — the product.

The team model, the identities, and every byte of HTTP. Depends on
``weave_core``; the reverse never happens (A2).

* :mod:`.team` — the coordinator and atomic claim, the fleet registry, the merge gate.
* :mod:`.devhost` — host registry and the outbound-only daemon (deployable #2).
* :mod:`.server` — the FastAPI app, routers, config, auth and the MCP sub-app.
* :mod:`.flows` — flow definitions and execution.
* :mod:`.ingress` — external events in, typed and deduped.
"""

from weave_core.version import __version__ as __version__
