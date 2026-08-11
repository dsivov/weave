"""`weave.model` — the artifact data model P2 adds on top of the carried engine.

Three things live here and they are related by one idea: **an artifact node
points at a document, it does not contain one** (A5).

- :mod:`weave.model.locator` — `Locator{repo, path, rev, anchor}`, the pointer
  itself, and the flat property names it takes on a graph node.
- :mod:`weave.model.project_layout` — the registry that turns a locator back
  into something real: a URL a human can click, or file content an agent can
  read. Workspace-scoped, because `resolve()` returns file content and a global
  registry would let one tenant read another's repository (R22a).
- :mod:`weave.model.answers` — the four canonical traversals behind `/ask/*`.

No HTTP lives here. The routers in `weave/server/routers/` are thin adapters
over these functions, and the MCP tools call the same ones (A9), so the human
and agent surfaces cannot answer the same question differently.
"""
