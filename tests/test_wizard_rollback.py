"""Rolling back a governance version restores the prior behaviour (M4 gate).

Not just the prior *bytes* — the prior **behaviour**. So the rollback tests
re-ask the enforcement layer the same questions `test_wizard_enforced.py` asks,
and require the answers to go back to what they were.

That distinction is the reason this file exists separately. A rollback that
restores a snapshot into the ledger while the runtime goes on enforcing the newer
version is the two-sources-of-truth failure A8 names, and it would pass any test
that only compared documents.
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


async def _run_wizard(engine, template, answers=None, *, approver="architect"):
    current = {
        kind: engine._load_current(WORKSPACE, kind, kind)
        for kind in ("rbac", "lifecycle")
    }
    for diff in propose_diffs(template, answers or {}, current=current):
        await engine.apply(WORKSPACE, diff, approver=approver,
                           reason="wizard run", role="architect")


@pytest.mark.asyncio
async def test_rolling_back_rbac_restores_the_prior_permission():
    """The behavioural assertion: a grant that was withdrawn comes back, and the
    enforcement layer says so."""
    engine, rbac, _lc = _engine()

    await _run_wizard(engine, "solo", {"agents_may_merge": True})
    assert rbac.check(WORKSPACE, "developer", "invoke", "MergeToMain").allowed is True

    await _run_wizard(engine, "solo", {"agents_may_merge": False})
    assert rbac.check(WORKSPACE, "developer", "invoke", "MergeToMain").allowed is False

    await engine.revert(
        WORKSPACE, "rbac", "rbac", 1,
        approver="manager", reason="that withdrawal was a mistake", role="manager",
    )

    assert rbac.check(WORKSPACE, "developer", "invoke", "MergeToMain").allowed is True, (
        "the ledger rolled back but the runtime kept enforcing the newer version"
    )


@pytest.mark.asyncio
async def test_rolling_back_lifecycle_restores_the_prior_gate():
    engine, _rbac, lifecycle = _engine()

    await _run_wizard(engine, "reviewed")
    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is False

    await _run_wizard(engine, "reviewed", {"developers_self_approve": True})
    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is True

    await engine.revert(
        WORKSPACE, "lifecycle", "lifecycle", 1,
        approver="architect", reason="restore the review gate", role="architect",
    )

    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is False, "the review gate did not come back"


@pytest.mark.asyncio
async def test_a_rollback_is_itself_a_signed_version_not_a_rewrite():
    """History moves forward. A rollback that erased the version it undid would
    destroy the record of a change someone made — and the audit question is
    usually "what happened", not "what is current"."""
    engine, _rbac, _lc = _engine()

    await _run_wizard(engine, "solo", {"agents_may_merge": True})
    await _run_wizard(engine, "solo", {"agents_may_merge": False})
    await engine.revert(WORKSPACE, "rbac", "rbac", 1,
                        approver="manager", reason="undo", role="manager")

    history = engine._studio.history(WORKSPACE, "rbac", "rbac")
    assert [v.version for v in history] == [1, 2, 3], (
        "a rollback rewrote history instead of appending to it"
    )
    assert history[-1].sign_off.approver == "manager"
    assert history[-1].sign_off.reason == "undo"


@pytest.mark.asyncio
async def test_rollback_restores_both_kinds_independently():
    """RBAC and lifecycle are separate artifacts with separate histories, so
    undoing one must not disturb the other — otherwise "roll back the permission
    change" would silently revert the workflow too."""
    engine, rbac, lifecycle = _engine()

    await _run_wizard(engine, "reviewed")
    await _run_wizard(engine, "reviewed", {"developers_self_approve": True})

    await engine.revert(WORKSPACE, "lifecycle", "lifecycle", 1,
                        approver="architect", reason="restore gate", role="architect")

    assert lifecycle.check(
        WORKSPACE, "Task", "review", "approved", role="developer"
    ).allowed is False
    # RBAC untouched by a lifecycle rollback.
    assert rbac.check(WORKSPACE, "developer", "invoke", "ClaimTask").allowed is True
    assert len(engine._studio.history(WORKSPACE, "rbac", "rbac")) == 2
