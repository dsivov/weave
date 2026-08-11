"""Two workers, two tasks, overlapping `touches` — exactly one winner (R6).

A regression test carried across with the code, for a review finding fixed in
the source on 2026-08-04. The bug was subtle and the fix is easy to undo by
accident, which is exactly why it is pinned here rather than trusted to memory.

**What went wrong.** The claim guards a *cross-task* invariant: a candidate's
``touches`` must not overlap any in-progress task. A per-task lock is therefore
not enough — two workers claiming two *different* tasks never contend for the
same lock, so both read the same busy-set, both see no overlap, and both
transition. Two developers then edit the same modules at once, and nothing
anywhere reports a problem until the merge.

The fix is a **workspace-keyed** lock: all claims in a workspace serialise,
whatever task they name. These tests fail if anyone narrows it back.

They also cover the plain one-task race, and the read-modify-write hazard on the
JSON store — where a stale snapshot could revert a task another claimer had just
written.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave.team import preset
from weave.team.coordinator import WeaveConflict, WeaveCoordinator
from weave.team.store import InMemoryWeaveTaskStore, JsonWeaveTaskStore


def _coordinator(store=None):
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    # Loads the preset's lifecycle machine straight into the service. Was
    # `preset.install("w", lifecycle_service=lifecycle)`; that installer now
    # signs through the governance ledger and needs a studio engine (D-034),
    # which a claim-race fixture has no business constructing. No assertion,
    # ordering or lock in this file changes — only how the machine is loaded.
    lifecycle.save("w", preset.load_part("lifecycle"))
    return WeaveCoordinator(store or InMemoryWeaveTaskStore(), lifecycle_service=lifecycle)


async def _settle(coros):
    """Run every claim concurrently; return (winners, conflicts)."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    winners = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, WeaveConflict)]
    other = [r for r in results if isinstance(r, Exception) and not isinstance(r, WeaveConflict)]
    assert not other, f"unexpected failures: {other}"
    return winners, conflicts


@pytest.mark.offline
@pytest.mark.asyncio
async def test_two_tasks_touching_the_same_module_produce_one_winner():
    """THE 2026-08-04 FINDING. Different tasks, overlapping touches, one winner.

    A per-task lock passes every other test in this file and fails this one.
    """
    c = _coordinator()
    c.create_task("w", "t1", title="refactor auth", touches=["auth.py"])
    c.create_task("w", "t2", title="fix auth bug", touches=["auth.py", "utils.py"])

    winners, conflicts = await _settle([
        c.claim("w", "t1", worker="dev-1", role="developer"),
        c.claim("w", "t2", worker="dev-2", role="developer"),
    ])

    assert len(winners) == 1, (
        f"{len(winners)} workers claimed overlapping work — the claim lock is not "
        "workspace-keyed, and two developers are now editing auth.py at once"
    )
    assert len(conflicts) == 1
    in_progress = [t for t in c._tasks.list("w") if t.status == "in_progress"]
    assert len(in_progress) == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_disjoint_touches_both_win():
    """The lock must not be so broad that it serialises unrelated work away.

    Workspace-keyed means claims are serialised, not refused: two tasks touching
    nothing in common must both be claimable.
    """
    c = _coordinator()
    c.create_task("w", "t1", title="auth", touches=["auth.py"])
    c.create_task("w", "t2", title="docs", touches=["README.md"])

    winners, conflicts = await _settle([
        c.claim("w", "t1", worker="dev-1", role="developer"),
        c.claim("w", "t2", worker="dev-2", role="developer"),
    ])
    assert len(winners) == 2 and not conflicts


@pytest.mark.offline
@pytest.mark.asyncio
async def test_one_task_many_claimers_produces_one_winner():
    c = _coordinator()
    c.create_task("w", "t1", title="the only task")

    winners, conflicts = await _settle([
        c.claim("w", "t1", worker=f"dev-{i}", role="developer") for i in range(8)
    ])
    assert len(winners) == 1 and len(conflicts) == 7
    assert winners[0].assignee in {f"dev-{i}" for i in range(8)}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_json_store_does_not_lose_a_claim_to_a_stale_snapshot():
    """The file-based path writes whole-file read-modify-write (A4, R10).

    Without serialisation a second claimer can read the pre-claim snapshot and
    write it back, silently reverting a task somebody already owns.
    """
    with tempfile.TemporaryDirectory() as d:
        store = JsonWeaveTaskStore(os.path.join(d, "tasks"))
        c = _coordinator(store)
        c.create_task("w", "t1", title="a", touches=["shared.py"])
        c.create_task("w", "t2", title="b", touches=["shared.py"])

        winners, _ = await _settle([
            c.claim("w", "t1", worker="dev-1", role="developer"),
            c.claim("w", "t2", worker="dev-2", role="developer"),
        ])
        assert len(winners) == 1

        # Re-read from disk: exactly one task is owned, and it kept its owner.
        reread = JsonWeaveTaskStore(os.path.join(d, "tasks")).list("w")
        owned = [t for t in reread if t.status == "in_progress"]
        assert len(owned) == 1
        assert owned[0].assignee == winners[0].assignee


@pytest.mark.offline
def test_the_claim_lock_is_keyed_by_workspace_not_by_task():
    """Guard the *shape* of the fix, so a rewrite cannot lose it quietly.

    The behavioural tests above need real concurrency to fail. This one reads the
    lock key directly, so narrowing it to a per-task key is caught even when the
    scheduler happens to interleave kindly.
    """
    import inspect

    params = list(inspect.signature(WeaveCoordinator._claim_lock).parameters)
    assert params == ["self", "workspace"], (
        f"_claim_lock takes {params}; anything task-shaped in there means two "
        "workers can claim overlapping tasks concurrently"
    )
    body = inspect.getsource(WeaveCoordinator._claim_lock).split('"""')[-1]
    assert "claim:{workspace}" in body, (
        "the claim lock key is no longer derived from the workspace alone"
    )
