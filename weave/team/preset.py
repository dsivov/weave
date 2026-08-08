"""Weave preset — the governance model as installable data (P0).

The methodology of a distributed AI dev team (Manager · Architect · Developer ·
Integrator) expressed as Weave primitives: a typed ontology, governed
actions, deny-by-default RBAC, role-gated lifecycle state machines, and an
advisory rules gate — adapted from ``ai_development_team`` and extended with the
planning + code artifacts Weave produces (PRD · RFC · Diagram · PullRequest) and
its two new actions (ClaimTask · OpenPullRequest).

The JSON lives under ``preset/`` as package data. :func:`validate` parses every
part through its schema so a broken preset is caught at build time;
:func:`install` writes the five governance layers into a workspace via the
existing services, turning the model from *authored* into *enforced*.
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


def install(
    workspace: str,
    *,
    ontology_service: Any = None,
    rules_service: Any = None,
    action_service: Any = None,
    rbac_service: Any = None,
    lifecycle_service: Any = None,
) -> Dict[str, Any]:
    """Install the five governance layers into *workspace* via the services.

    Each layer is optional (a missing service is skipped, so the installer works
    in reduced test setups). Returns a report of the versions written. Seed
    entities (the role nodes) are created separately by the project bootstrap /
    REST installer, since they need the graph store.
    """
    p = load_preset()
    report: Dict[str, Any] = {"workspace": workspace}

    if ontology_service is not None and "ontology" in p:
        report["ontology"] = ontology_service.save(workspace, p["ontology"]).version
    if rules_service is not None and "rules" in p:
        r = p["rules"]
        report["rules"] = rules_service.save(
            workspace, r.get("dsl", ""), r.get("concepts", {}),
            enabled=bool(r.get("enabled", True))).version
    if action_service is not None and "actions" in p:
        report["actions"] = action_service.save(workspace, p["actions"]).version
    if rbac_service is not None and "rbac" in p:
        report["rbac"] = rbac_service.save(workspace, p["rbac"]).version
    if lifecycle_service is not None and "lifecycle" in p:
        report["lifecycle"] = lifecycle_service.save(workspace, p["lifecycle"]).version

    logger.info(f"Weave preset installed into '{workspace}': {report}")
    return report


def seed_entities() -> List[Dict[str, Any]]:
    """The role nodes to create in the graph (used by the project bootstrap)."""
    return load_part("seed").get("entities", []) if load_part("seed") else []
