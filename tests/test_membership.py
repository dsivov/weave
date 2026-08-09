"""Workspace membership is an explicit grant, and it is enforced (R14, A6).

Multi-user without scoping is a shared login with extra steps. A user sees and
can act on **only** the workspaces they were granted, and the grant is checked
against the authenticated identity rather than anything the client sends.

The second half of the M1 gate lives here too: a `developer` receives 403 on an
architect-only governed action. That is not a user-store property — it is the
governance layer the fork carried — so it is exercised against the real RBAC
service rather than a stand-in, which is the only way to know the two are
actually wired together.
"""

from __future__ import annotations

import pytest

from weave.server.users import (
    ACTIVE,
    DISABLED,
    InMemoryUserStore,
    UserService,
)
from weave.team import preset
from weave_core.governance.rbac import InMemoryRbacStore, RbacService


def _service() -> UserService:
    return UserService(InMemoryUserStore())


# ── grants ───────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_a_user_sees_only_granted_workspaces():
    svc = _service()
    alice = svc.create("alice", "a-good-password", workspaces=["alpha", "beta"])
    assert alice.workspaces == ["alpha", "beta"]
    assert alice.may_access("alpha")
    assert alice.may_access("beta")
    assert not alice.may_access("gamma"), "a workspace nobody granted was visible"


@pytest.mark.offline
def test_a_user_with_no_grants_sees_nothing():
    """The default is nothing. Anything else makes a new account a liability."""
    alice = _service().create("alice", "a-good-password")
    assert alice.workspaces == []
    assert not alice.may_access("alpha")
    assert not alice.may_access("default")


@pytest.mark.offline
def test_a_disabled_user_may_access_nothing_they_were_granted():
    """Disabling is not a label — it must actually take the access away."""
    svc = _service()
    alice = svc.create("alice", "a-good-password", workspaces=["alpha"])
    assert alice.may_access("alpha")
    svc.update(alice.id, status=DISABLED)
    assert not svc.require(alice.id).may_access("alpha")


@pytest.mark.offline
def test_grants_can_be_replaced_and_revoked():
    svc = _service()
    alice = svc.create("alice", "a-good-password", workspaces=["alpha", "beta"])
    svc.set_workspaces(alice.id, ["beta", "gamma"], granted_by="root")
    reread = svc.require(alice.id)
    assert reread.workspaces == ["beta", "gamma"]
    assert not reread.may_access("alpha"), "a revoked grant still worked"
    svc.set_workspaces(alice.id, [], granted_by="root")
    assert svc.require(alice.id).workspaces == []


@pytest.mark.offline
def test_regranting_preserves_the_provenance_of_grants_that_stay():
    """"Why does this person have access" gets asked during incidents.

    Editing an unrelated grant must not rewrite the history of the others.
    """
    svc = _service()
    alice = svc.create("alice", "a-good-password", workspaces=["alpha"], granted_by="root")
    first = svc.require(alice.id).memberships[0]
    svc.set_workspaces(alice.id, ["alpha", "beta"], granted_by="someone-else")
    kept = {m.workspace: m for m in svc.require(alice.id).memberships}
    assert kept["alpha"].granted_by == "root"
    assert kept["alpha"].granted_at == first.granted_at
    assert kept["beta"].granted_by == "someone-else"


@pytest.mark.offline
def test_duplicate_grants_collapse():
    svc = _service()
    alice = svc.create("alice", "a-good-password")
    svc.set_workspaces(alice.id, ["alpha", "alpha", "beta", "alpha"])
    assert svc.require(alice.id).workspaces == ["alpha", "beta"]


@pytest.mark.offline
def test_members_of_lists_only_active_grantees():
    svc = _service()
    svc.create("alice", "a-good-password", workspaces=["alpha"])
    svc.create("bob", "a-good-password", workspaces=["alpha", "beta"])
    carol = svc.create("carol", "a-good-password", workspaces=["alpha"])
    svc.update(carol.id, status=DISABLED)

    assert [u.username for u in svc.members_of("alpha")] == ["alice", "bob"]
    assert [u.username for u in svc.members_of("beta")] == ["bob"]
    assert svc.members_of("gamma") == []


# ── the governed action: a developer is refused what an architect may do ─────


#: An action the shipped Weave preset grants to `architect` and to nobody else
#: below `manager` — so a developer being refused it is a policy decision, not a
#: missing entry.
ARCHITECT_ONLY = ("invoke", "PublishPlan")

#: One the preset grants to developers, so the fixture is provably wired up.
DEVELOPER_MAY = ("invoke", "ClaimTask")


def _rbac() -> RbacService:
    """The **real** Weave governance policy, installed from the shipped preset.

    Not a hand-written fixture. A stand-in policy would prove that RbacService
    can say no, which was never in doubt; installing the preset proves the
    policy this product actually ships refuses the thing the gate names.
    """
    rbac = RbacService(InMemoryRbacStore())
    preset.install("alpha", rbac_service=rbac)
    return rbac


@pytest.mark.offline
def test_the_preset_actually_installed():
    """Guard the fixture: deny-by-default makes an empty policy look like a pass."""
    summary = _rbac().get_summary("alpha")
    assert summary["exists"], "no policy installed — every later denial would be vacuous"
    assert {"manager", "architect", "developer", "integrator"} <= set(summary["roles"])


@pytest.mark.offline
def test_a_developer_is_denied_an_architect_only_action():
    """The M1 gate's 403, against the governance layer the product ships.

    The assertion that carries the weight is the *allowed* one: with
    deny-by-default, "developer is refused" would also be true of a typo in the
    action name. Architect being allowed is what makes the denial meaningful.
    """
    rbac = _rbac()
    verb, target = ARCHITECT_ONLY

    allowed = rbac.check("alpha", "architect", verb, target)
    refused = rbac.check("alpha", "developer", verb, target)

    assert allowed.allowed, f"architect could not perform its own action: {allowed.reason}"
    assert not refused.allowed, "a developer performed an architect-only action"
    assert refused.reason, "a denial with no reason is not an answer anybody can act on"


@pytest.mark.offline
def test_the_developer_role_is_not_simply_denied_everything():
    """Otherwise the test above passes on a policy that refuses all comers."""
    verb, target = DEVELOPER_MAY
    assert _rbac().check("alpha", "developer", verb, target).allowed


@pytest.mark.offline
def test_an_unknown_role_is_denied():
    verb, target = DEVELOPER_MAY
    assert not _rbac().check("alpha", "nobody-in-particular", verb, target).allowed


@pytest.mark.offline
def test_the_role_checked_is_the_stored_one():
    """The principal comes from the record, never from the request (A6, R15)."""
    users = _service()
    rbac = _rbac()
    verb, target = ARCHITECT_ONLY
    dev = users.create("dev", "a-good-password", role="developer", workspaces=["alpha"])

    assert not rbac.check("alpha", dev.role, verb, target).allowed

    users.update(dev.id, role="architect")
    promoted = users.require(dev.id)
    assert rbac.check("alpha", promoted.role, verb, target).allowed, (
        "promoting a user in the store did not change what they may do"
    )


@pytest.mark.offline
def test_a_grant_does_not_imply_a_role():
    """Membership answers *where*; the role answers *what*. Conflating them is
    how somebody gets architect rights by being added to a workspace."""
    users = _service()
    rbac = _rbac()
    verb, target = ARCHITECT_ONLY
    dev = users.create("dev", "a-good-password", role="developer",
                       workspaces=["alpha", "beta"])
    assert dev.may_access("alpha")
    assert not rbac.check("alpha", dev.role, verb, target).allowed
