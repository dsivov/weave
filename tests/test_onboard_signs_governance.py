"""Onboarding installs governance through the signed ledger (A8, D-032).

The M4 review found this by reading the enforcement path, and it is worse than
"drift". `routers/actions.py` runs
`resolve principal → RBAC → lifecycle → rules gate → side effect`, mapping a gate
REJECT to 422 — so a rule installed by onboarding **is enforced by the runtime**.
Writing it straight through `rules_service.save` left it with no signature and no
version, which makes A8's first sentence — *what the runtime enforces is the
signed ledger version* — false for that rule now, not eventually.

The wizard already did this correctly (P4). Onboarding was the second write path,
and two paths for the same artifact kinds with different guarantees is the shape
that produces exactly this.

These tests are about the property rather than the endpoint: **every writer of a
ledger-owned artifact kind leaves a version behind.** The endpoint needs an LLM
to run, so the writer is exercised directly — what matters is that the artifact
and its ledger entry move together, not which HTTP handler asked.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from weave_core.governance.ontology import InMemoryOntologyStore, OntologyService
from weave_core.governance.rules import InMemoryRuleStore, RulesService
from weave_core.studio.schema import ArtifactDiff
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"
_ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "weave" / "server" / "routers"

ONTOLOGY = {
    "name": "onboarded",
    "object_types": [{"name": "Widget", "properties": []}],
    "link_types": [],
}


def _engine():
    ontology = OntologyService(InMemoryOntologyStore(now=lambda: 1.0))
    rules = RulesService(InMemoryRuleStore(now=lambda: 1.0))
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        ontology_service=ontology,
        rules_service=rules,
        now=lambda: 1.0,
    )
    return engine, ontology, rules


async def _sign(engine, kind: str, after: dict, approver: str = "alice") -> int:
    """The helper `onboard_apply` now uses, in miniature."""
    before = engine._load_current(WORKSPACE, kind, kind)
    from_version = before.get("version") if before else None
    diff = ArtifactDiff(
        kind=kind, artifact_id=kind,
        to_version=int(from_version or 0) + 1, from_version=from_version,
        delta={"before": before or {}, "after": after},
        behaviour_changed=True, origin="authoring",
    )
    result = await engine.apply(
        WORKSPACE, diff, approver=approver, reason="onboarding", role="admin")
    return result["version"]


# ── the gap, stated as the property that was violated ────────────────────────


def test_a_direct_service_save_leaves_no_ledger_entry():
    """The defect, reproduced rather than described.

    This is what onboarding used to do. The artifact exists and the runtime will
    enforce it; the ledger has never heard of it.
    """
    engine, ontology, _rules = _engine()

    ontology.save(WORKSPACE, ONTOLOGY)

    assert ontology.get_summary(WORKSPACE)["exists"] is True, "the artifact is live"
    assert engine._studio.history(WORKSPACE, "ontology", "ontology") == [], (
        "…and the ledger has no record of it — that is the A8 gap"
    )


@pytest.mark.asyncio
async def test_signing_leaves_both_the_artifact_and_its_version():
    """The fix: the artifact and the ledger entry move together."""
    engine, ontology, _rules = _engine()

    version = await _sign(engine, "ontology", ONTOLOGY)

    assert ontology.get_summary(WORKSPACE)["version"] == version
    history = engine._studio.history(WORKSPACE, "ontology", "ontology")
    assert [v.version for v in history] == [version]
    assert history[0].sign_off.approver == "alice"
    assert history[0].sign_off.reason


@pytest.mark.asyncio
async def test_an_onboarded_rule_carries_a_signature():
    """Rules are the ones that bite: `routers/actions.py` runs the rules gate and
    maps REJECT to 422, so an unsigned rule is an enforced rule nobody approved.
    """
    engine, _ontology, rules = _engine()

    version = await _sign(
        engine, "rule",
        {"dsl": 'rule "flag big"\nwhen\n    percent > 0.1\nthen\n    flag()\n',
         "concepts": {}, "enabled": True},
    )

    history = engine._studio.history(WORKSPACE, "rule", "rule")
    assert [v.version for v in history] == [version]
    assert history[0].sign_off.approver == "alice"
    assert rules.store.load(WORKSPACE) is not None


@pytest.mark.asyncio
async def test_an_onboarded_artifact_can_be_rolled_back():
    """The practical consequence of having a version at all. Before the fix there
    was nothing to roll back *to* — an onboarding mistake was permanent unless
    someone hand-edited the store."""
    engine, ontology, _rules = _engine()

    await _sign(engine, "ontology", ONTOLOGY)
    await _sign(engine, "ontology", {
        "name": "onboarded",
        "object_types": [{"name": "Widget", "properties": []},
                         {"name": "Sprocket", "properties": []}],
        "link_types": [],
    })

    await engine.revert(WORKSPACE, "ontology", "ontology", 1,
                        approver="manager", reason="undo the onboarding edit",
                        role="manager")

    names = {o["name"] for o in ontology.get_summary(WORKSPACE)["object_types"]}
    assert names == {"Widget"}


# ── the class: no router writes a ledger-owned kind behind the ledger's back ──


#: Artifact kinds the signed ledger owns, by the variable a router holds them in.
LEDGER_OWNED = {
    "ontology_service": "ontology",
    "rules_service": "rule",
    "rbac_service": "rbac",
    "lifecycle_service": "lifecycle",
}

#: Routers allowed to call `save()`/`delete()` on a bare `service`, because what
#: they hold is **not** a ledger-owned artifact. Each entry is a claim someone
#: made deliberately, and the comment is the claim.
#:
#: The rule runs the other way round on purpose (M5 review). An earlier version
#: listed the four *governance* editors and treated everything else as innocent —
#: so a new `routers/policy.py` holding its service as `service` would resolve to
#: no kind and sail through. Defaulting to "offender unless annotated" costs a
#: comment on a false positive and catches an unsigned governance path on a false
#: negative, which is the trade worth making.
#:
#: This is the third layer of the same lesson: the exclusion list hid the hole,
#: the matcher could not see what it excluded, and the *reach* was a hand-kept
#: list that a new file would not be on.
NON_LEDGER_SERVICE_ROUTERS = {
    "flows.py": "flow definitions — versioned by the flow store, not the ledger",
    "diagrams.py": "diagrams — the Studio owns their versioning directly",
    "users.py": "user accounts — A14 records, not governed artifacts",
    "projects.py": "ProjectLayout registrations — not a governance artifact",
    "studio.py": "the Studio itself — it *is* the ledger writer",
    "workspaces.py": "signs through the engine; asserted separately by D-032",
}


def test_no_router_writes_a_ledger_owned_artifact_directly():
    """The structural rule, so another write path cannot appear quietly.

    **The exclusion list is gone (D-033).** It used to exempt `rbac.py`,
    `ontology.py`, `lifecycle.py`, `rules.py` and `studio.py` on the reasoning
    that they "are the direct surface, and the Studio composes them" — which was
    a statement of intent, not of what A8 says. Four of the five were the biggest
    write paths in the product: `POST /rbac` changed what the runtime enforced
    with no signature, and `DELETE /rbac` returned a workspace to permissive with
    no record at all.

    A guard whose exclusion list contains the largest hole is worse than no
    guard, because it reads as coverage. So the rule is now uniform: **no router
    calls `save()` or `delete()` on a ledger-owned service.** Reads are untouched
    and common — `get_summary`, `store.load`, `check`, `attach`.
    """
    offenders = []

    for path in sorted(_ROUTERS.glob("*.py")):
        exempt = path.name in NON_LEDGER_SERVICE_ROUTERS
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in ("save", "delete"):
                continue
            owner = getattr(func.value, "id", None)

            named = LEDGER_OWNED.get(owner)
            if named is not None:
                offenders.append(
                    f"{path.name}:{node.lineno} — {owner}.{func.attr}() changes "
                    f"'{named}' without a ledger version"
                )
            elif owner == "service" and not exempt:
                # Unknown by default. A router holding a bare `service` it writes
                # through is presumed to be editing a governed artifact until
                # someone says otherwise in NON_LEDGER_SERVICE_ROUTERS.
                offenders.append(
                    f"{path.name}:{node.lineno} — service.{func.attr}() writes "
                    "directly; if this is not a ledger-owned artifact, add the "
                    "file to NON_LEDGER_SERVICE_ROUTERS with the reason"
                )

    assert not offenders, (
        "a router installs governance behind the ledger's back; what the runtime "
        "enforces must be the signed ledger version (A8, D-032):\n  "
        + "\n  ".join(offenders)
    )


def test_onboard_apply_refuses_rather_than_writing_unsigned():
    """If the Studio engine is missing, onboarding must refuse — not fall back to
    the direct write. A fallback is how a removed second path comes back."""
    source = (_ROUTERS / "workspaces.py").read_text(encoding="utf-8")
    assert "studio_engine is None" in source
    assert "D-032" in source, "the reason should survive next to the code"
