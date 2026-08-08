"""The HTTP routers — one thin adapter per resource.

A router's job is to authenticate, validate, call a service function, and turn
the verdict it gets back into a status code. Anything it decides for itself is a
second source of truth (A9).

The Ollama-compatible chat surface and the web-scraper routes are not here: both
left with the modules they fronted (D-008).
"""

from weave.server.routers.documents import router as document_router
from weave.server.routers.query import router as query_router
from weave.server.routers.graph import router as graph_router

__all__ = ["document_router", "query_router", "graph_router"]
