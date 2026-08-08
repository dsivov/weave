"""Governance — every action passes through here, and none may bypass it (A6).

* :mod:`.rbac` — who may do what.
* :mod:`.lifecycle` — which state transitions are legal, and for which role.
* :mod:`.actions` — the governed action catalogue and its handlers.
* :mod:`.rules` — the advisory rules engine and the single verdict gate.
* :mod:`.ontology` — object and link types, and their validation.

These return **verdicts**; they never raise HTTP. Translating a verdict into a
403, a 409 or a 200 is the server's job, which is what keeps the engine usable
without one (A2).
"""
