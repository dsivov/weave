"""Weave preset — the governance model as installable data (P0).

The methodology of a distributed AI dev team (Manager · Architect · Developer ·
Integrator) expressed as Weave primitives: a typed ontology, governed
actions, deny-by-default RBAC, role-gated lifecycle state machines, and an
advisory rules gate — adapted from ``ai_development_team`` and extended with the
planning + code artifacts Weave produces (PRD · RFC · Diagram · PullRequest) and
its two new actions (ClaimTask · OpenPullRequest).

The JSON lives under ``preset/`` as package data. :func:`validate` parses every
part through its schema so a broken preset is caught at build time;
:func:`install` writes the five governance layers into a workspace, turning the
model from *authored* into *enforced*.

**It writes them through the signed ledger, and that is not incidental.** All
five layers — ontology, rules, actions, RBAC, lifecycle — are ``DIFF_KINDS``
members, and A8 says *what the runtime enforces is the signed ledger version*.
An installer that called ``rules_service.save`` directly produced a rule the
rules gate enforces with no signature, no version and no way to roll it back —
which is D-032's finding exactly, and it survived here after being fixed in the
wizard because this installer writes through a **helper**, not through a store
call a reader (or a guard) recognises as one.

That is the same lesson one layer further out: the guard matches
``<store>.save(...)`` inside a router, and ``preset.install(...)`` is neither.
So the fix belongs here rather than at each caller — one installer, signing, and
every surface that onboards a workspace inherits it (A9).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from weave_core.utils import logger

PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preset")
PARTS = ("ontology", "actions", "rbac", "lifecycle", "rules", "seed")


def _strip_comment(obj: Any) -> Any:
    """Drop the top-level ``_comment`` key (documentation only)."""
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k != "_comment"}
    return obj


def load_part(name: str) -> Optional[Dict[str, Any]]:
    """Load one preset part (comment stripped), or None if absent."""
    path = os.path.join(PRESET_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return _strip_comment(json.load(fh))


def load_preset() -> Dict[str, Dict[str, Any]]:
    """The whole preset as ``{part: data}`` for present parts."""
    return {name: p for name in PARTS if (p := load_part(name)) is not None}


def summary() -> Dict[str, Any]:
    """Human/UI-facing counts of what the preset defines."""
    p = load_preset()
    onto = p.get("ontology", {})
    return {
        "name": onto.get("name", "weave"),
        "version": onto.get("version", 1),
        "object_types": len(onto.get("object_types", [])),
        "link_types": len(onto.get("link_types", [])),
        "actions": len(p.get("actions", {}).get("actions", [])),
        "roles": len(p.get("rbac", {}).get("roles", {})),
        "machines": len(p.get("lifecycle", {}).get("machines", {})),
        "concepts": len(p.get("rules", {}).get("concepts", {})),
        "seed_roles": len(p.get("seed", {}).get("entities", [])),
    }


def validate() -> List[str]:
    """Parse every part through its schema and lint it. Returns problems
    (empty ⇒ the preset is well-formed and installable)."""
    from weave_core.governance.actions import ActionCatalog
    from weave_core.governance.lifecycle import Lifecycle
    from weave_core.governance.ontology.schema import Ontology
    from weave_core.governance.rbac import RbacPolicy
    from weave_core.governance.rules.store import validate_policy

    p = load_preset()
    problems: List[str] = []

    if "ontology" in p:
        problems += [f"ontology: {x}" for x in Ontology.from_dict(p["ontology"]).lint()]
    if "actions" in p:
        problems += [f"actions: {x}" for x in ActionCatalog.from_dict(p["actions"]).lint()]
    if "lifecycle" in p:
        problems += [f"lifecycle: {x}" for x in Lifecycle.from_dict(p["lifecycle"]).lint()]
    if "rbac" in p:
        problems += [f"rbac: {x}" for x in RbacPolicy.from_dict(p["rbac"]).lint()]
    if "rules" in p:
        r = p["rules"]
        try:
            validate_policy(r.get("dsl", ""), r.get("concepts", {}))
        except ValueError as e:
            problems.append(f"rules: {e}")
    return problems


#: Preset part → the ledger ``kind`` it is signed as. The order is the order
#: they are installed in: RBAC last, because it is the layer that can lock the
#: installing principal out, and a half-installed workspace is easier to finish
#: than a locked one.
LAYERS = (
    ("ontology", "ontology"),
    ("actions", "action"),
    ("lifecycle", "lifecycle"),
    ("rules", "rule"),
    ("rbac", "rbac"),
)


async def install(
    workspace: str,
    engine: Any,
    *,
    approver: str,
    reason: str = "onboarding: install the Weave governance preset",
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """Install the five governance layers into *workspace*, each signed.

    *engine* is a :class:`weave_core.studio.service.DiffEngine`, which already
    holds every governance service — so this takes one collaborator rather than
    five, and cannot be handed a service the ledger does not know about.

    ``approver`` is required and comes from the authenticated principal (A6). A
    preset install rewrites who may do what; leaving it unattributed makes *"who
    took away my access"* unanswerable, which is the question the ledger exists
    to answer.

    A layer whose service is missing from the engine raises rather than being
    skipped. Silent skipping is how a workspace ends up believing it is governed
    while one layer is absent — the previous signature made every layer optional,
    and the reduced setups that motivated it were tests, not deployments.

    Returns the versions written. Seed entities (the role nodes) are created
    separately by the caller, since they need the graph store.
    """
    if not approver:
        raise ValueError("preset install needs an approver — governance is attributed")
    if engine is None:
        raise ValueError(
            "preset install needs a studio engine: the five governance layers are "
            "ledger-owned, and installing them unsigned would leave the runtime "
            "enforcing a policy with no version and no way back (A8)")

    p = load_preset()
    report: Dict[str, Any] = {"workspace": workspace}

    for part, kind in LAYERS:
        if part not in p:
            continue
        applied = await engine.sign(
            workspace, kind, p[part],
            approver=approver, reason=reason, role=role)
        report[part] = applied.get("version")

    logger.info(f"Weave preset installed into '{workspace}': {report}")
    return report


def seed_entities() -> List[Dict[str, Any]]:
    """The role nodes to create in the graph (used by the project bootstrap)."""
    return load_part("seed").get("entities", []) if load_part("seed") else []
