"""A wizard change is *enforced*, not merely recorded (M4 gate).

The gate is behavioural and deliberately so: an RBAC change must be observed as
**a 403 that was a 200 before**, and a lifecycle change as **a 409** — on the
next request, with no restart. Anything less proves only that a file was written,
which is precisely the second-source-of-truth A8 exists to prevent.

So these tests do not inspect the ledger and conclude it worked. They ask the
governance services the same questions the runtime asks, before and after, and
require the answer to have changed.
"""

from __future__ import annotations

import pytest

from weave.wizards import propose_diffs
from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave_core.governance.rbac import InMemoryRbacStore, RbacService
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"


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


async def _run_wizard(engine, template: str, answers=None, *, approver="architect"):
    """One wizard run, end to end, through the ledger."""
    current = {
        kind: engine._load_current(WORKSPACE, kind, kind)
        for kind in ("rbac", "lifecycle")
    }
    diffs = propose_diffs(template, answers or {}, current=current)
    results = []
    for diff in diffs:
        results.append(
            await engine.apply(
                WORKSPACE, diff, approver=approver,
                reason="wizard run", role="architect",
            )
        )
    return results


# ── an RBAC change is a 403 that was a 200 ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_wizard_rbac_change_turns_a_200_into_a_403():
    """The gate criterion, asked of the enforcement layer rather than the ledger.

    `solo` grants the developer role `OpenPullRequest`; `reviewed` grants it too,
    but withdrawing a role from the policy withdraws its grants — so a role that
    the second run does not install stops being allowed to act.
    """
    engine, rbac, _lc = _engine()

    # No policy at all is permissive — the "200 before" the gate speaks of.
    assert rbac.check(WORKSPACE, "integrator", "invoke", "MergeToMain").allowed is True

    await _run_wizard(engine, "solo")

    # `solo` installs manager + developer only. The integrator now holds no
    # grants, so the same call is refused — a 403 that was a 200.
    after = rbac.check(WORKSPACE, "integrator", "invoke", "MergeToMain")
    assert after.allowed is False, (
        "the wizard's policy is in the ledger but the runtime is not enforcing it"
    )


@pytest.mark.asyncio
async def test_the_change_is_visible_on_the_next_call_with_no_restart():
    """No process was restarted and no file was reloaded between these two
    assertions — the service is asked twice and answers differently."""
    engine, rbac, _lc = _engine()
    await _run_wizard(engine, "reviewed")

    assert rbac.check(WORKSPACE, "developer", "invoke", "ClaimTask").allowed is True
    assert rbac.check(WORKSPACE, "developer", "invoke", "MergeToMain").allowed is False

    # A second wizard run, same process, widening the developer's grants.
    await _run_wizard(engine, "solo", {"agents_may_merge": True})

    assert rbac.check(WORKSPACE, "developer", "invoke", "MergeToMain").allowed is True, (
        "a governance change needed a restart to take effect"
    )


@pytest.mark.asyncio
async def test_an_answer_narrows_what_is_installed():
    """`roles_present` removes a role the team does not have. The grants go with
    it — an unassigned role is not the same as an unrestricted one."""
    engine, rbac, _lc = _engine()

    await _run_wizard(
        engine, "reviewed",
        {"roles_present": ["manager", "architect", "developer"]},
    )

    roles = rbac.get_summary(WORKSPACE)["roles"]
    assert "integrator" not in roles
    assert rbac.check(WORKSPACE, "integrator", "invoke", "Deploy").allowed is False


# ── a lifecycle change is a 409 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_wizard_lifecycle_change_refuses_an_illegal_transition():
    """The 409 half of the gate. `reviewed` has no pending → done edge, so the
    jump that skips review is refused — which is the whole point of the shape."""
    engine, _rbac, lifecycle = _engine()

    # Permissive before any lifecycle exists.
    assert lifecycle.check(WORKSPACE, "Task", "pending", "done").allowed is True

    await _run_wizard(engine, "reviewed")

    refused = lifecycle.check(WORKSPACE, "Task", "pending", "done")
    assert refused.allowed is False, "the lifecycle is recorded but not enforced"

    # …and the legal step still works, or the machine would just be broken.
    assert lifecycle.check(
        WORKSPACE, "Task", "pending", "in_progress", role="developer"
    ).allowed is True


@pytest.mark.asyncio
async def test_the_review_gate_is_role_gated_not_merely_ordered():
    """A developer cannot approve their own work under `reviewed`. Ordering
    without a role gate would let one person walk a task through every state."""
    engine, _rbac, lifecycle = _engine()
    await _run_wizard(engine, "reviewed")

    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is False
    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="architect"
    ).allowed is True


@pytest.mark.asyncio
async def test_answering_yes_to_self_approval_removes_the_gate_and_records_it():
    """The template warns what this costs, and the answer is honoured — but it
    lands as a signed diff like anything else, so "who removed the review gate"
    has an answer."""
    engine, _rbac, lifecycle = _engine()

    await _run_wizard(engine, "reviewed", {"developers_self_approve": True},
                      approver="manager")

    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is True

    history = engine._studio.history(WORKSPACE, "lifecycle", "lifecycle")
    assert history[-1].sign_off.approver == "manager"
    assert history[-1].behaviour_changed is True


# ── it is all in the ledger, attributed ──────────────────────────────────────


@pytest.mark.asyncio
async def test_both_changes_appear_in_history_with_a_signature_and_a_diff():
    """A gate criterion. Governance that changed with no record of who or why is
    what the ledger exists to prevent."""
    engine, _rbac, _lc = _engine()

    await _run_wizard(engine, "reviewed", approver="architect")

    for kind in ("rbac", "lifecycle"):
        history = engine._studio.history(WORKSPACE, kind, kind)
        assert len(history) == 1, f"{kind} was not recorded"
        version = history[0]
        assert version.sign_off.approver == "architect"
        assert version.sign_off.reason
        assert version.snapshot, "a version with no snapshot cannot be rolled back to"


@pytest.mark.asyncio
async def test_a_governance_change_cannot_be_unattributed():
    """`behaviour_changed` is always true for these kinds, so sign-off demands an
    approver and a reason."""
    engine, _rbac, _lc = _engine()
    diffs = propose_diffs("solo", {})

    with pytest.raises(ValueError):
        await engine.apply(WORKSPACE, diffs[0], approver="", reason="")
