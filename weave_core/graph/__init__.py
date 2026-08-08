"""The graph engine — the quadruple store and CGR3 retrieve → rank → reason.

* :mod:`.engine` — the storage-agnostic engine: ingest, index, query.
* :mod:`.quadruple` — the ``(h, r, t, rc)`` layer and iterative reasoning.
* :mod:`.base` — the storage roles the engine is written against.
* :mod:`.storage` — the three adapters that fill them (A4).

Nothing here imports HTTP or the product layer (A2).
"""
