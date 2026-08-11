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


@pytest.mark.offline
def test_install_writes_all_layers():
    svc = _services()
    report = preset.install("proj", **svc)
    assert report["ontology"] == 1 and report["rules"] == 1
    assert report["actions"] == 1 and report["rbac"] == 1 and report["lifecycle"] == 1


@pytest.mark.offline
def test_rbac_enforces_the_pipeline():
    svc = _services()
    preset.install("proj", **svc)
    rbac = svc["rbac_service"]
    # a developer may claim, not merge
    assert rbac.check("proj", "developer", "invoke", "ClaimTask").allowed
    assert not rbac.check("proj", "developer", "invoke", "MergeToMain").allowed
    # the manager is unrestricted; an unknown role is denied
    assert rbac.check("proj", "manager", "invoke", "MergeToMain").allowed
    assert not rbac.check("proj", "intern", "invoke", "ClaimTask").allowed


@pytest.mark.offline
def test_lifecycle_enforces_task_states():
    svc = _services()
    preset.install("proj", **svc)
    lc = svc["lifecycle_service"]
    # the claim is legal for a developer; an illegal jump is refused
    assert lc.check("proj", "Task", "pending", "in_progress", role="developer").allowed
    assert not lc.check("proj", "Task", "pending", "done").allowed
    # approval is the architect/manager's, not the developer's
    assert not lc.check("proj", "Task", "review", "approved", role="developer").allowed
    assert lc.check("proj", "Task", "review", "approved", role="architect").allowed
