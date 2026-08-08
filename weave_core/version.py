"""Version constants — held in the engine so the dependency direction stays inward.

``__api_version__`` describes the HTTP contract the server serves and the UI is
built against, but it lives *here* rather than in :mod:`weave.server` because the
model connectors stamp it into their ``User-Agent`` headers. With it in the
server package, ``weave_core.llm.openai`` would have to import ``weave.server`` —
the engine reaching into the product layer, which A2 forbids. The constant is
re-exported from :mod:`weave.server` so callers there are unchanged.
"""

#: The Weave product version.
__version__ = "0.1.0"

#: The HTTP API contract version, checked against the built UI at boot.
__api_version__ = "0271"
