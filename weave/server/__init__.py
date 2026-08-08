"""The HTTP surface — the only place HTTP exists in this repository (A2).

The FastAPI app, its routers, configuration, authentication and the mounted MCP
sub-app. Every route is a thin adapter over a service function that the CLI and
the MCP tools call too, so the human and agent surfaces cannot answer the same
question differently (A9).

``__api_version__`` is defined in :mod:`weave_core.version` and re-exported here:
the model connectors stamp it into their ``User-Agent``, and the engine may not
import the product layer.
"""

from weave_core.version import __api_version__ as __api_version__
