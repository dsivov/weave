"""RBAC and lifecycle are signed ledger kinds, not config files (R35, A8).

A8 says what the runtime enforces is the signed ledger version, and that roles,
RBAC and lifecycle have **no server-file config path**. The obvious wizard design
breaks that: interview the team, write a config file, restart. That file is then
a second source of truth — and worse, one the runtime may never read.

So governance changes travel the route every other artifact already takes:
propose → diff → sign → version → history → rollback. These tests assert the two
new kinds behave exactly like the established ones, because "exactly like" is the
property that keeps there from being a second path.

The behavioural half of the gate — an RBAC change observed as a 403 that was a
200, a lifecycle change observed as a 409 — lives in `test_wizard_enforced.py`.
"""

from __future__ import annotations

import pytest

from weave_core.governance.lifecycle import (
    InMemoryLifecycleStore,
    LifecycleService,
)
from weave_core.governance.rbac import InMemoryRbacStore, RbacService
from weave_core.studio.schema import DIFF_KINDS, ArtifactDiff
from weave_core.studio.service import DiffEngine, StaleWrite
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"

RBAC_V1 = {
    "name": "team",
    "roles": {
        "developer": {"grants": ["read:*", "update:task"]},
        "architect": {"grants": ["read:*", "update:*", "create:*"]},
    },
}
RBAC_V2 = {
    "name": "team",
    "roles": {
        "developer": {"grants": ["read:*"]},          # update:task withdrawn
        "architect": {"grants": ["read:*", "update:*", "create:*"]},
    },
}
LIFECYCLE_V1 = {
    "name": "team",
    "machines": {
        "Task": {
            "states": ["pending", "in_progress", "done"],
            "initial": "pending",
            "transitions": [
                {"from": "pending", "to": "in_progress"},
                {"from": "in_progress", "to": "done"},
            ],
        }
    },
}


def _engine():
    rbac = RbacService(InMemoryRbacStore())
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        rbac_service=rbac,
        lifecycle_service=lifecycle,
        now=lambda: 1.0,
    )
    return engine, rbac, lifecycle


def _diff(kind: str, after: dict, from_version):
    return ArtifactDiff(
        kind=kind,
        artifact_id=kind,
        to_version=int(from_version or 0) + 1,
        from_version=from_version,
        delta={"before": {}, "after": after},
        behaviour_changed=True,
        origin="authoring",
    )


# ── they are ledger kinds at all ─────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("kind", ["rbac", "lifecycle"])
def test_the_kind_is_registered(kind):
    assert kind in DIFF_KINDS


@pytest.mark.offline
@pytest.mark.parametrize("kind", ["rbac", "lifecycle"])
def test_a_diff_of_that_kind_validates(kind):
    """`ArtifactDiff` rejects an unknown kind, so this is the gate the wizard's
    proposal has to pass before anything else happens."""
    _diff(kind, {}, None)   # must not raise


# ── propose → sign → version, the same route as every other artifact ─────────


@pytest.mark.asyncio
async def test_an_rbac_policy_is_signed_into_a_version(_=None):
    engine, rbac, _lifecycle = _engine()

    result = await engine.apply(
        WORKSPACE, _diff("rbac", RBAC_V1, None),
        approver="architect", reason="initial policy", role="architect",
    )

    assert result["version"] == 1
    assert result["sign_off"]["approver"] == "architect"
    # The runtime reads what was signed — not a file, and with no restart.
    assert rbac.get_summary(WORKSPACE)["roles"]["developer"] == ["read:*", "update:task"]


@pytest.mark.asyncio
async def test_a_lifecycle_is_signed_into_a_version():
    engine, _rbac, lifecycle = _engine()

    result = await engine.apply(
        WORKSPACE, _diff("lifecycle", LIFECYCLE_V1, None),
        approver="architect", reason="initial lifecycle", role="architect",
    )

    assert result["version"] == 1
    assert "Task" in lifecycle.get_summary(WORKSPACE)["machines"]


@pytest.mark.asyncio
async def test_a_governance_change_appears_in_ledger_history_with_a_diff():
    """A gate criterion: the change is in history with an attributed signature
    and a diff. Governance that changed with no record of who or why is the
    thing the ledger exists to prevent."""
    engine, _rbac, _lc = _engine()

    await engine.apply(WORKSPACE, _diff("rbac", RBAC_V1, None),
                       approver="architect", reason="initial", role="architect")
    await engine.apply(WORKSPACE, _diff("rbac", RBAC_V2, 1),
                       approver="manager", reason="withdraw developer writes",
                       role="manager")

    history = engine._studio.history(WORKSPACE, "rbac", "rbac")
    assert [v.version for v in history] == [1, 2]
    assert history[1].sign_off.approver == "manager"
    assert history[1].sign_off.reason == "withdraw developer writes"
    assert history[1].snapshot["roles"]["developer"]["grants"] == ["read:*"]


@pytest.mark.asyncio
async def test_a_behaviour_changing_governance_edit_requires_a_signature():
    """`behaviour_changed` demands an approver and a reason. Withdrawing a grant
    is the definition of a behaviour change, and an unattributed one is how
    "who took away my access" becomes unanswerable."""
    engine, _rbac, _lc = _engine()

    with pytest.raises(ValueError):
        await engine.apply(WORKSPACE, _diff("rbac", RBAC_V1, None),
                           approver="", reason="")


# ── the same concurrency guard as every other kind (P3.3) ────────────────────


@pytest.mark.asyncio
async def test_a_stale_governance_write_is_refused_like_any_other():
    """Two architects editing the policy is exactly the case where a silent
    overwrite is worst — one of them believes they withdrew an access that is
    still granted."""
    engine, rbac, _lc = _engine()
    await engine.apply(WORKSPACE, _diff("rbac", RBAC_V1, None),
                       approver="architect", reason="initial", role="architect")

    await engine.apply(WORKSPACE, _diff("rbac", RBAC_V2, 1),
                       approver="manager", reason="withdraw", role="manager")

    with pytest.raises(StaleWrite):
        await engine.apply(WORKSPACE, _diff("rbac", RBAC_V1, 1),
                           approver="architect", reason="restore", role="architect")

    # The manager's withdrawal stands; the stale restore did not land.
    assert rbac.get_summary(WORKSPACE)["roles"]["developer"] == ["read:*"]


# ── a malformed policy cannot be signed off ──────────────────────────────────


@pytest.mark.asyncio
async def test_an_unenforceable_policy_is_refused_rather_than_signed():
    """`RbacService.save` validates. Signing something that cannot be enforced
    would put the ledger and the runtime out of step — which is the same
    two-sources-of-truth failure A8 names, arrived at from the other side."""
    engine, rbac, _lc = _engine()

    with pytest.raises(Exception):
        await engine.apply(
            WORKSPACE, _diff("rbac", {"name": "bad", "roles": "not-a-mapping"}, None),
            approver="architect", reason="oops", role="architect",
        )

    assert rbac.get_summary(WORKSPACE)["exists"] is False


@pytest.mark.asyncio
async def test_the_engine_refuses_a_kind_it_has_no_service_for():
    """A `DiffEngine` built without the governance services must say so rather
    than appear to succeed."""
    engine = DiffEngine(studio_store=InMemoryStudioStore(), now=lambda: 1.0)

    with pytest.raises(ValueError, match="no RBAC service"):
        await engine.apply(WORKSPACE, _diff("rbac", RBAC_V1, None),
                           approver="a", reason="r")
