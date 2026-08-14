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

#: **Every carried file that asserts the claim protocol, each pinned.**
#:
#: This was one file — `test_claim_race.py` — and that was the defect the M6
#: review found. The gate criterion is *the pre-existing claim **tests** pass
#: unmodified* (R41, DRP §326), plural, and the pin covered one of the three. So
#: a developer could declare, truthfully, exactly what the pin watches and still
#: leave two carried files unaccounted for — which is what happened under D-034.
#:
#: **A guard's reach silently redefines the claim it is trusted to enforce.**
#: That is the fifth instance of one lesson in this project: the exclusion list
#: hid the hole (D-033), the matcher could not see what it excluded, the reach
#: was a hand-kept list (W12), the *justification* was untrue, and now the *set*
#: was smaller than the criterion. Each was found by taking the previous fix
#: seriously enough to test it rather than believe it.
#:
#: If one of these fails, do **not** update the hash to make it pass. Either a
#: claim test was edited — which the gate forbids — or it was legitimately
#: changed by a reviewed decision, in which case the `D-NN` comes first and the
#: constant moves with it, in the same commit, with the reason.
CLAIM_TESTS_SHA256 = {
    # The races themselves: two workers, overlapping `touches`, one winner.
    "test_claim_race.py":
        "e4f81dc6df5f005e7cb88cdd90819f1b43fe2a033eda1a7e4724cf542af82c90",
    # The scheduler and the claim: ready-set, deps, the lock, 409 on the loser.
    "test_weave_coordinator.py":
        "02071dd30e4176e2861218ac682e10a5371665769067b577511e0a05f95c8b90",
    # The same protocol through HTTP: create → ready → brief → atomic claim,
    # with RBAC and the lifecycle role gate in front of it.
    "test_weave_api.py":
        "6668ca421e445de97d84f0fb900083362867f9abe7c9e9b52fe24f52dbd5cbc6",
}

#: The pre-D-034 hashes, kept so the move stays auditable rather than merely
#: asserted. **All three moved**, not the one that was declared: D-034 made
#: `preset.install()` sign through the governance ledger, so every fixture that
#: used it as a shortcut for "load me a preset layer" had to say what it actually
#: wanted. `test_claim_race.py` and `test_weave_coordinator.py` became
#: `lifecycle.save("w", preset.load_part("lifecycle"))`; `test_weave_api.py`
#: installs governance through `POST /weave/bootstrap` instead, which exercises
#: the signed installer rather than stepping around it.
#:
#: The manager ratified this on evidence rather than on the argument: running
#: both fixture paths side by side produces a **byte-identical lifecycle
#: machine**, differing only in an `updated_at` timestamp 3.5 ms apart.
CLAIM_TESTS_SHA256_BEFORE_D034 = {
    "test_claim_race.py":
        "ac4cf323c116d1c9c7874ec62cdf739af620844ba080c94b02064cc80210cae2",
}

#: A carried test is one that came over with the code at P0. Authored-here tests
#: that happen to call `claim()` are not part of the gate criterion and are not
#: pinned — `test_membership.py` (P1) and `test_supervisor*.py` (P5) among them.
#: Listed by name because git history is not available to a test run, and a
#: wrong entry here is caught by the coverage check below rather than trusted.
NOT_CARRIED_BUT_MENTIONS_CLAIMING = {
    "test_claim_protocol_unchanged.py",   # this file
    "test_membership.py",                 # P1 — workspace grants
    "test_devhost_outbound.py",           # P6 — A15, no claim assertions
    # P10.1 — claims a task only to get one far enough to have a review
    # recorded against it; every assertion is about what recording writes.
    "test_recording_writes_what_the_answer_reads.py",
}


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 1 · the carried tests themselves ─────────────────────────────────────────


@pytest.mark.parametrize("filename", sorted(CLAIM_TESTS_SHA256))
def test_the_claim_tests_still_exist(filename):
    assert (_TESTS / filename).exists(), (
        f"tests/{filename} is gone — the gate is that the carried claim tests "
        "pass unmodified, which they cannot do if one is deleted"
    )


@pytest.mark.parametrize("filename", sorted(CLAIM_TESTS_SHA256))
def test_the_claim_tests_are_byte_for_byte_unmodified(filename):
    """The gate criterion, mechanised — for **each** carried claim file.

    Deliberately a hash rather than a spot-check: the point is that *nothing*
    moved, and a targeted assertion would only notice the parts someone thought
    to assert.
    """
    actual = _sha(_TESTS / filename)
    assert actual == CLAIM_TESTS_SHA256[filename], (
        f"tests/{filename} has changed.\n\n"
        "  The gate is that the carried claim tests pass UNMODIFIED. If a "
        "feature needed one edited, that is the signal to stop and report — not "
        "to adjust the test.\n\n"
        f"  expected {CLAIM_TESTS_SHA256[filename]}\n  actual   {actual}\n"
    )


def test_the_pin_reaches_every_test_that_asserts_a_claim():
    """**The reach itself, asserted — which is the finding this file exists to
    stop recurring.**

    Pinning three files is only better than pinning one until a fourth carried
    file grows a claim assertion. So the set is not trusted: every test file that
    exercises the claim path must be pinned or explicitly listed as
    authored-here. A new file is an offender until someone says which it is.

    Same shape as the governance guard in
    `tests/test_onboard_signs_governance.py` — default to *offender unless
    annotated*, because a false positive costs a line and a false negative costs
    the guarantee.
    """
    unaccounted = []
    for path in sorted(_TESTS.glob("test_*.py")):
        if path.name in CLAIM_TESTS_SHA256 or path.name in NOT_CARRIED_BUT_MENTIONS_CLAIMING:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        asserts_a_claim = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "claim"
            for node in ast.walk(tree)
        ) or "/claim" in source

        if asserts_a_claim:
            unaccounted.append(path.name)

    assert not unaccounted, (
        "these test files exercise the claim protocol and are neither pinned nor "
        "declared authored-here:\n  " + "\n  ".join(unaccounted)
        + "\n\n  The gate criterion is *the carried claim tests pass "
        "unmodified* — plural. Add each to CLAIM_TESTS_SHA256 if it was carried "
        "at P0, or to NOT_CARRIED_BUT_MENTIONS_CLAIMING if it was authored here."
    )


#: The properties each carried file is kept *for*. A hash proves nothing moved;
#: it does not prove that what was there was worth keeping — and a file could be
#: rewritten wholesale, re-pinned, and still satisfy every hash in this module.
REQUIRED_TESTS = {
    "test_claim_race.py": {
        "test_two_tasks_touching_the_same_module_produce_one_winner",
        "test_disjoint_touches_both_win",
        "test_one_task_many_claimers_produces_one_winner",
        "test_the_json_store_does_not_lose_a_claim_to_a_stale_snapshot",
        "test_the_claim_lock_is_keyed_by_workspace_not_by_task",
    },
    "test_weave_coordinator.py": {
        "test_ready_set_honours_deps_touches_and_priority",
        "test_claim_is_atomic_one_winner",
        "test_claim_respects_role_gate",
        "test_touches_conflict_defers_the_second_task",
        "test_claim_blocked_on_unmet_dependency",
    },
    "test_weave_api.py": {
        "test_create_ready_brief_claim",
        "test_role_gate_blocks_manager_claim",
    },
}


@pytest.mark.parametrize("filename", sorted(REQUIRED_TESTS))
def test_the_claim_tests_cover_the_races_that_matter(filename):
    """The properties a fleet actually depends on, per file."""
    source = (_TESTS / filename).read_text(encoding="utf-8")
    names = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    missing = REQUIRED_TESTS[filename] - names
    assert not missing, (
        f"tests/{filename} no longer contains: {', '.join(sorted(missing))}.\n"
        "  A hash only proves the file did not move. This is what it is kept for."
    )


def test_every_pinned_file_declares_what_it_is_kept_for():
    """The two maps must cover the same files — otherwise a file could be pinned
    with nothing said about why, which is how a rewrite-and-re-pin passes."""
    assert set(CLAIM_TESTS_SHA256) == set(REQUIRED_TESTS), (
        "CLAIM_TESTS_SHA256 and REQUIRED_TESTS disagree: "
        f"{set(CLAIM_TESTS_SHA256) ^ set(REQUIRED_TESTS)}"
    )


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
