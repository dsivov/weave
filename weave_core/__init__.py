"""Weave core — the engine.

The graph, the governance verdicts, the signed ledger, the event port and the
model connectors. It knows nothing about HTTP, nothing about users, and nothing
about the product layer above it: ``weave_core`` imports nothing from ``weave``
and no HTTP framework (A2). That is what lets the engine be tested without a
server, and what keeps the three storage paths a configuration choice rather
than three codebases.

    from weave_core import WeaveEngine, QueryParam

``WeaveGraph`` — the quadruple ``(h, r, t, rc)`` layer and CGR3
retrieve → rank → reason — is resolved lazily, because it lives in
:mod:`weave_core.graph.quadruple`, which imports from this package. Eager import
here would close the cycle.
"""

from weave_core.graph.engine import WeaveEngine as WeaveEngine, QueryParam as QueryParam
from weave_core.graph.types import (
    RelationContext as RelationContext,
    ContextNode as ContextNode,
    ContextEdge as ContextEdge,
)
from weave_core.version import __version__ as __version__, __api_version__ as __api_version__


def __getattr__(name: str):
    # Resolved after this package has finished initialising, so
    # ``from weave_core import WeaveGraph`` works without a circular import.
    if name == "WeaveGraph":
        from weave_core.graph.quadruple import WeaveGraph

        return WeaveGraph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
