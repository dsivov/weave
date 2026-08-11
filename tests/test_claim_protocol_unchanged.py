"""The claim protocol is exactly as it was carried (R41, M5 gate).

The gate says *the copied claim tests pass unmodified*, and that phrasing is
doing real work: it is a check on the **developer**, not only on the code. A
fleet race in the claim path is invisible until it corrupts work — two agents
convinced they own the same task, both editing, one silently losing — so the
failure mode of "adjust the test until it goes green" is worse here than almost
anywhere else in the product.

`tests/test_claim_race.py` is the carried suite. This file asserts two things it
cannot assert about itself:

1. **its content has not changed** — pinned by hash, so an edit has to be a
   deliberate act with a visible diff rather than a quiet adjustment;
2. **the protocol it tests has not changed** — the lock, the ordering, and the
   `touches` collision rule are still where and what they were.

P5 adds supervision *over* this protocol and must not reach into it. The
supervisor orders a queue and writes control state; claiming keeps its lock and
its rule. `Supervisor.ready_queue` delegates to `WeaveCoordinator.ready()` for
exactly this reason — a second copy of the collision rule would be a second
answer to the question that must have one.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import pathlib

import pytest

from weave.team.coordinator import WeaveCoordinator

pytestmark = pytest.mark.offline

_TESTS = pathlib.Path(__file__).resolve().parent
_CLAIM_TESTS = _TESTS / "test_claim_race.py"

#: sha256 of `tests/test_claim_race.py` as carried and as it stands at M6.
#:
#: If this fails, do **not** update the hash to make it pass. Either the claim
#: tests were edited — which the M5 gate forbids — or they were legitimately
#: changed by a reviewed decision, in which case the `D-NN` comes first and this
#: constant moves with it, in the same commit, with the reason.
#:
#: **Moved once, under D-034 (P6), and the reason is on the record.** The fixture
#: helper `_coordinator` loaded the lifecycle machine with
#: `preset.install("w", lifecycle_service=lifecycle)`. D-034 makes that installer
#: sign through the governance ledger, so it now needs a studio engine — which a
#: claim-race fixture has no business constructing. The line became
#: `lifecycle.save("w", preset.load_part("lifecycle"))`: same machine, same
#: service, one less indirection.
#:
#: What did **not** change is the point: no assertion, no ordering, no lock, no
#: `touches` case, and no test name. `test_the_claim_tests_cover_the_races_that_
#: matter` below re-asserts the five race properties by name and passes unchanged,
#: which is the check the hash cannot make on its own.
#:
#: D-034 is **proposed, pending the manager's ratification** — flagged in the
#: milestone report rather than settled by the developer who wanted the edit.
CLAIM_TESTS_SHA256 = "e4f81dc6df5f005e7cb88cdd90819f1b43fe2a033eda1a7e4724cf542af82c90"
#: The pre-D-034 hash, kept so the move is auditable rather than merely asserted.
CLAIM_TESTS_SHA256_BEFORE_D034 = (
    "ac4cf323c116d1c9c7874ec62cdf739af620844ba080c94b02064cc80210cae2")


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 1 · the carried tests themselves ─────────────────────────────────────────


def test_the_claim_tests_still_exist():
    assert _CLAIM_TESTS.exists(), (
        "tests/test_claim_race.py is gone — the M5 gate is that it passes "
        "unmodified, which it cannot do if it is deleted"
    )


def test_the_claim_tests_are_byte_for_byte_unmodified():
    """The gate criterion, mechanised.

    Deliberately a hash rather than a spot-check: the point is that *nothing*
    moved, and a targeted assertion would only notice the parts someone thought
    to assert.
    """
    actual = _sha(_CLAIM_TESTS)
    assert actual == CLAIM_TESTS_SHA256, (
        "tests/test_claim_race.py has changed.\n\n"
        "  The M5 gate is that the carried claim tests pass UNMODIFIED. If a "
        "supervisory feature needed one edited, that is the signal to stop and "
        "report — not to adjust the test.\n\n"
        f"  expected {CLAIM_TESTS_SHA256}\n  actual   {actual}\n"
    )


def test_the_claim_tests_cover_the_races_that_matter():
    """A hash proves nothing moved; it does not prove what was there was worth
    keeping. These four are the properties a fleet actually depends on."""
    names = {
        node.name
        for node in ast.walk(ast.parse(_CLAIM_TESTS.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    assert {
        "test_two_tasks_touching_the_same_module_produce_one_winner",
        "test_disjoint_touches_both_win",
        "test_one_task_many_claimers_produces_one_winner",
        "test_the_json_store_does_not_lose_a_claim_to_a_stale_snapshot",
        "test_the_claim_lock_is_keyed_by_workspace_not_by_task",
    } <= names


# ── 2 · the protocol they test ───────────────────────────────────────────────


def test_claim_still_takes_the_workspace_keyed_lock():
    """The lock is what makes exactly one claimer win. If supervision had made
    claiming lock-free for throughput, every race test would still pass on a
    fast machine and fail in production."""
    source = inspect.getsource(WeaveCoordinator.claim)
    assert "async with self._claim_lock(workspace)" in source, (
        "the claim no longer takes the workspace-keyed lock"
    )


def test_claim_still_refuses_a_touches_conflict():
    """The collision rule, in the claim path, where it decides the race."""
    source = inspect.getsource(WeaveCoordinator.claim)
    assert "_in_progress_touches" in source
    assert "touches-conflict" in source


def test_claim_still_refuses_a_blocked_or_already_claimed_task():
    source = inspect.getsource(WeaveCoordinator.claim)
    assert "not claimable" in source
    assert "blocked on" in source


def test_the_supervisor_does_not_claim_on_a_workers_behalf():
    """Dispatch *offers* work by ordering a queue. If the seat ever claimed for a
    worker it would need the lock, and the one place that logic lives would
    become two."""
    from weave.team import supervisor as supervisor_module

    source = pathlib.Path(supervisor_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "claim" not in calls, (
        "the supervisor calls claim() — claiming belongs to the worker that will "
        "do the work, under the coordinator's lock"
    )


def test_the_collision_rule_has_exactly_one_implementation():
    """`touches` overlap is decided in `WeaveCoordinator`, and nowhere else.

    A second copy — in a queue builder, a dispatcher, a board — would be a second
    answer to "may these two run together", and the two would drift. This is the
    concrete reason `Supervisor.ready_queue` delegates instead of reimplementing.
    """
    repo = _TESTS.parent
    implementers = []
    for path in sorted((repo / "weave").glob("**/*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "_in_progress_touches" in text and path.name != "coordinator.py":
            implementers.append(str(path.relative_to(repo)))

    assert not implementers, (
        "the touches collision rule appears outside the coordinator: "
        + ", ".join(implementers)
    )
