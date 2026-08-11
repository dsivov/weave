"""P0 test gate — the Weave governance preset is well-formed and enforceable.

The methodology-as-data installs into a workspace and the shipped governance
layers then enforce it for real: RBAC (403), lifecycle (409), and the rules gate.
Offline; in-memory service backends.
"""

from __future__ import annotations

import pytest

from weave_core.governance.actions import ActionService, InMemoryActionStore
from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave_core.governance.ontology import InMemoryOntologyStore, OntologyService
from weave_core.governance.rbac import InMemoryRbacStore, RbacService
from weave_core.governance.rules import InMemoryRuleStore, RulesService
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import InMemoryStudioStore
from weave.team import preset


@pytest.mark.offline
def test_preset_validates():
    assert preset.validate() == []


@pytest.mark.offline
def test_preset_summary_counts():
    s = preset.summary()
    assert s["name"] == "weave"
    assert s["object_types"] == 18        # + PRD · RFC · Diagram · PullRequest · Worker · DevHost · Environment · IntegrationRun, and P2's Feature · Review · Insight · Question (R19)
    assert s["actions"] == 15             # incl. ClaimTask · OpenPullRequest · PublishPlan · RegisterWorker · RegisterDevHost · Deploy · RunIntegration · Promote
    assert s["roles"] == 4               # manager · architect · developer · integrator
    assert s["machines"] == 4            # Task · ChangeRequest · PullRequest · ADR
    assert s["seed_roles"] == 4


@pytest.mark.offline
def test_new_objects_and_actions_present():
    p = preset.load_preset()
    objs = {o["name"] for o in p["ontology"]["object_types"]}
    assert {"PRD", "RFC", "Diagram", "PullRequest", "DevHost"} <= objs
    acts = {a["name"] for a in p["actions"]["actions"]}
    assert {"ClaimTask", "OpenPullRequest"} <= acts


def _services():
    return dict(
        ontology_service=OntologyService(InMemoryOntologyStore()),
        rules_service=RulesService(InMemoryRuleStore()),
        action_service=ActionService(InMemoryActionStore()),
        rbac_service=RbacService(InMemoryRbacStore()),
        lifecycle_service=LifecycleService(InMemoryLifecycleStore()),
    )


def _engine(svc):
    """The installer's one collaborator — it already holds all five services."""
    return DiffEngine(studio_store=InMemoryStudioStore(), **svc)


async def _install(svc, **kw):
    return await preset.install(
        "proj", _engine(svc), approver=kw.pop("approver", "alice"), **kw)


@pytest.mark.offline
async def test_install_writes_all_layers():
    svc = _services()
    report = await _install(svc)
    assert report["ontology"] == 1 and report["rules"] == 1
    assert report["actions"] == 1 and report["rbac"] == 1 and report["lifecycle"] == 1


@pytest.mark.offline
async def test_every_installed_layer_leaves_a_signed_ledger_version():
    """A8, at the installer rather than at each surface.

    All five preset layers are `DIFF_KINDS` members, and the rules layer is
    enforced by the gate the moment it lands. Installing one with no version left
    the runtime enforcing a policy that could not be attributed or rolled back —
    D-032's finding, surviving in this installer because it writes through a
    helper rather than through a store call a guard recognises (D-034).
    """
    svc = _services()
    engine = _engine(svc)
    await preset.install("proj", engine, approver="alice", reason="onboarding")

    assert {kind for _part, kind in preset.LAYERS} == {
        "ontology", "rule", "action", "rbac", "lifecycle"}, (
        "a layer was added to the installer — it needs a ledger kind too"
    )
    for _part, kind in preset.LAYERS:
        versions = engine.history("proj", kind, kind)
        assert versions, f"{kind} was installed with no ledger version"
        sign_off = versions[-1]["sign_off"]
        assert sign_off["approver"] == "alice", f"{kind} is unattributed"
        assert sign_off["reason"], f"{kind} was signed with no reason"


@pytest.mark.offline
async def test_install_refuses_rather_than_writing_unsigned():
    """The refusal is the property. An installer that falls back to a direct
    write when no ledger is available reintroduces the defect precisely when the
    ledger is missing — which is the worst moment for it."""
    svc = _services()
    with pytest.raises(ValueError, match="studio engine"):
        await preset.install("proj", None, approver="alice")
    assert svc["rbac_service"].store.load("proj") is None, (
        "the refused install still wrote a policy"
    )


@pytest.mark.offline
async def test_install_refuses_an_unattributed_change():
    """A6: the principal comes from the authenticated identity. A preset install
    rewrites who may do what, and 'who took away my access' has to be
    answerable."""
    svc = _services()
    with pytest.raises(ValueError, match="approver"):
        await preset.install("proj", _engine(svc), approver="")


@pytest.mark.offline
async def test_rbac_enforces_the_pipeline():
    svc = _services()
    await _install(svc)
    rbac = svc["rbac_service"]
    # a developer may claim, not merge
    assert rbac.check("proj", "developer", "invoke", "ClaimTask").allowed
    assert not rbac.check("proj", "developer", "invoke", "MergeToMain").allowed
    # the manager is unrestricted; an unknown role is denied
    assert rbac.check("proj", "manager", "invoke", "MergeToMain").allowed
    assert not rbac.check("proj", "intern", "invoke", "ClaimTask").allowed


@pytest.mark.offline
async def test_lifecycle_enforces_task_states():
    svc = _services()
    await _install(svc)
    lc = svc["lifecycle_service"]
    # the claim is legal for a developer; an illegal jump is refused
    assert lc.check("proj", "Task", "pending", "in_progress", role="developer").allowed
    assert not lc.check("proj", "Task", "pending", "done").allowed
    # approval is the architect/manager's, not the developer's
    assert not lc.check("proj", "Task", "review", "approved", role="developer").allowed
    assert lc.check("proj", "Task", "review", "approved", role="architect").allowed
