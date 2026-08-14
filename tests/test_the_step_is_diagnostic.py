"""A supervisor can tell working from stuck — and nothing governs on it (P10.5).

dsivov supervises a developer session in tmux and asked whether Weave gives the
same view of a containerised agent. Measured rather than answered from the
design:

    worker heartbeat        → one field: current_task
    claude -p               → capture_output=True
    its stdout              → truncated to 400 chars, remainder discarded
    docker logs on the host → the worker loop, NOT the conversation

So `current_task` answers *what* and nothing answers *where*. The fleet now says
`building · 4m`, and **the duration is the point**: "building" alone does not
answer *is it stuck?*, which is the entire reason for the field. `building` is
the only step measured in minutes — it is the `claude -p` call.

**The constraint this file exists for.** The step is *diagnostic*, never
governed. The task lifecycle is the governed state and the signed ledger
enforces it; a second field that says where a task is — one the runtime reads and
nobody signed — is A8's failure arriving in a new place. This is exactly the kind
of field that acquires a reader six months later *because it happens to be
there*, and by then it is load-bearing and unsigned. So the rule is asserted as a
class, now, while there is nothing to fix.

**What is deliberately not here.** Transcript streaming. It answers *trust*, and
the review gate, the tests and the PR are what answer trust; it also needs a
governance answer nobody has, since an agent's reasoning is the most revealing
artifact in the system and RBAC has no notion of who may read it. W29 records
that *destroying* the transcript is a defect on its own terms — separable, and it
must not arrive as a side effect of this change. It does not: the step is known
at the call site, before the work runs and before any output exists.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from weave.team.worker import COMMIT_SUBJECT_MAX, WORKER_STEPS, run_worker

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent


# ── the step is reported ─────────────────────────────────────────────────────


def test_the_vocabulary_is_the_loop_and_the_loop_is_the_vocabulary():
    """Neither may grow a step the other does not have.

    A name the loop never sends is a state a supervisor waits for forever; a step
    the loop sends that is not declared is a word nobody defined.
    """
    source = pathlib.Path(run_worker.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    sent = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "beat" and node.args
                and isinstance(node.args[0], ast.Constant)):
            sent.add(node.args[0].value)

    assert sent, "the loop no longer reports any step at all"
    assert sent <= set(WORKER_STEPS), (
        f"the loop sends steps that are not declared: {sorted(sent - set(WORKER_STEPS))}"
    )
    assert set(WORKER_STEPS) - sent == set(), (
        "these steps are declared but never sent, so a supervisor would wait for "
        f"a state that cannot arrive: {sorted(set(WORKER_STEPS) - sent)}"
    )


def test_the_long_step_is_reported_before_the_long_call():
    """`building` after `code_runner(...)` would be a field that only ever tells
    you what already finished — useless for the one question it exists to
    answer."""
    source = pathlib.Path(run_worker.__code__.co_filename).read_text(encoding="utf-8")
    assert source.index('beat("building")') < source.index("result = code_runner(brief)")
    assert source.index('beat("testing")') < source.index("tests_passed = git.run_tests()")


def test_the_registry_times_the_step_not_the_heartbeat(monkeypatch):
    """`building · 4m` has to be four minutes in `building`, not four minutes
    since the last beat — so the clock restarts only when the step changes."""
    import asyncio

    from weave.team.workers import InMemoryWeaveWorkerStore, WorkerRegistry

    now = {"t": 1000.0}
    registry = WorkerRegistry(InMemoryWeaveWorkerStore(), now=lambda: now["t"])
    asyncio.run(registry.register("w", "dev-1", role="developer"))

    registry.heartbeat("w", "dev-1", step="building")
    now["t"] += 240.0
    registry.heartbeat("w", "dev-1", step="building")      # same step, later beat
    view = registry.get("w", "dev-1")
    assert view["step"] == "building"
    assert view["step_seconds"] == pytest.approx(240.0), (
        "the clock restarted on a heartbeat rather than on a change of step"
    )

    registry.heartbeat("w", "dev-1", step="testing")
    assert registry.get("w", "dev-1")["step_seconds"] == pytest.approx(0.0)


def test_a_worker_that_never_reports_a_step_has_no_duration(monkeypatch):
    """`None`, not zero. Zero would read as *just started*, which is a claim."""
    import asyncio

    from weave.team.workers import InMemoryWeaveWorkerStore, WorkerRegistry

    registry = WorkerRegistry(InMemoryWeaveWorkerStore())
    asyncio.run(registry.register("w", "dev-1", role="developer"))
    assert registry.get("w", "dev-1")["step_seconds"] is None


# ── and it governs nothing ───────────────────────────────────────────────────


def _step_compared_to_a_literal(tree: ast.AST) -> list[int]:
    """Lines where something reads a `step` and compares it to a constant.

    **Comparison to a literal is the line**, not any mention of the field. The
    registry compares the incoming step against the stored one to decide whether
    to restart the clock — old against new, which is plumbing. `if
    worker["step"] == "building"` is something else entirely: it reads *meaning*
    into a field nobody signed, and it is a second answer to where a task is.

    **The `.get("step")` form is included, and it was missing.** The first
    version recognised `w.step`, `w["step"]` and a bare `step`, and a negative
    control walked straight through it: the heartbeat returns a *dict*, so the
    natural way to read this field is `response.get("step") == "building"` — the
    single most likely spelling of the exact mistake the rule forbids. A guard
    that covers every form but the probable one is not a guard.
    """
    def _reads_step(s: ast.AST) -> bool:
        if isinstance(s, ast.Attribute) and s.attr == "step":
            return True
        if (isinstance(s, ast.Subscript) and isinstance(s.slice, ast.Constant)
                and s.slice.value == "step"):
            return True
        if isinstance(s, ast.Name) and s.id == "step":
            return True
        # `something.get("step")` / `getattr(something, "step")`
        if isinstance(s, ast.Call):
            name = getattr(s.func, "attr", getattr(s.func, "id", ""))
            if name in {"get", "getattr"}:
                return any(isinstance(a, ast.Constant) and a.value == "step"
                           for a in s.args)
        return False

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        reads_step = any(_reads_step(s) for s in sides)
        against_literal = any(
            isinstance(s, ast.Constant) and isinstance(s.value, str) for s in sides
        )
        if reads_step and against_literal:
            hits.append(node.lineno)
    return hits


def test_nothing_branches_on_the_step():
    """**The constraint.**

    Not "nothing branches on it today" — nothing *may*. A supervisor field that
    starts gating work is an unsigned second lifecycle, and the first such branch
    will look entirely reasonable at the time.
    """
    offenders = []
    for path in sorted(_REPO.glob("weave/**/*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line in _step_compared_to_a_literal(tree):
            offenders.append(f"{path.relative_to(_REPO)}:{line}")
    assert not offenders, (
        "something compares a worker's step against a literal:\n  "
        + "\n  ".join(offenders)
        + "\n\n  The step is diagnostic liveness. The governed state of a task is "
        "its lifecycle, enforced by the signed ledger (A8). A field the runtime "
        "reads that nobody signed is the failure this rule exists to prevent."
    )


def test_the_step_reaches_no_lifecycle_or_rbac_call():
    """The other shape of the same mistake: not comparing it, but *passing* it
    into governance and letting that do the comparing."""
    offenders = []
    for path in sorted(_REPO.glob("weave/**/*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in {"check", "advance_task", "allowed", "may", "authorize"}:
                continue
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                if ((isinstance(arg, ast.Attribute) and arg.attr == "step")
                        or (isinstance(arg, ast.Name) and arg.id == "step")):
                    offenders.append(f"{path.relative_to(_REPO)}:{node.lineno}: {name}(…step…)")
    assert not offenders, (
        "the diagnostic step is passed into a governance decision:\n  "
        + "\n  ".join(offenders)
    )


# ── a commit subject is a subject ────────────────────────────────────────────


def test_the_commit_subject_is_the_task_not_the_transcript():
    """`record_commit(subject=result.summary)` put up to 400 characters of model
    stdout into the subject line of a permanent artifact — and **the 400 was
    never a chosen number**, it was the runner's truncation of its own output
    leaking into an artifact it had nothing to do with.
    """
    # **Parsed, not grepped.** The first version matched text and flagged the
    # comment in `worker.py` that *explains* this fix — the third time in this
    # phase that a text matcher could not tell code from the prose describing it.
    source = pathlib.Path(run_worker.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "record_commit"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "subject":
                continue
            rendered = ast.unparse(keyword.value)
            if "summary" in rendered:
                offenders.append(f"line {node.lineno}: subject={rendered}")
    assert not offenders, (
        "the model's account of its work is being used as a commit subject: "
        + "; ".join(offenders)
    )
    assert "COMMIT_SUBJECT_MAX" in source
    assert COMMIT_SUBJECT_MAX == 72, "72 is the git convention for a subject line"


def test_the_model_s_account_is_still_recorded():
    """It moves, it is not dropped. The account answers *why* and belongs in the
    decision trace, which already receives it — a subject answers *what*."""
    source = pathlib.Path(run_worker.__code__.co_filename).read_text(encoding="utf-8")
    assert "decision_trace=result.summary" in source, (
        "the model's summary is no longer recorded anywhere at all"
    )


# ── the fleet shows it ───────────────────────────────────────────────────────

_UI = _REPO / "weave-ui" / "src"


def test_both_fleet_views_show_the_step_through_one_formatter():
    """The board and the project panel both display workers. Two spellings of
    `building · 4m` would drift, and the duration is the part that would rot
    first — one of them rounding differently is a supervisor seeing two answers.
    """
    for rel in ("features/next/pages/WeaveBoard.tsx",
                "features/next/WeaveProjectPanel.tsx"):
        code = (_UI / rel).read_text(encoding="utf-8")
        assert "workerStep(w)" in code, f"{rel} does not show the worker's step"
        assert "step_seconds" not in code, (
            f"{rel} formats the duration itself instead of using the shared "
            "formatter — that is the second implementation"
        )


def test_a_worker_with_no_step_shows_nothing_rather_than_a_placeholder():
    """`workerStep` returns an empty string, not "unknown" — a placeholder in a
    liveness display reads as a state the worker is in."""
    code = (_UI / "api" / "weave.ts").read_text(encoding="utf-8")
    formatter = code[code.index("export const workerStep"):]
    formatter = formatter[:formatter.index("\n}")]
    assert "if (!w.step) return ''" in formatter


def test_the_ui_does_not_branch_on_the_step_either():
    """A8 does not stop at the server. A `step === 'building'` in a component is
    the same unsigned second lifecycle, read by a different reader."""
    offenders = []
    for path in sorted(_UI.rglob("*.ts")) + sorted(_UI.rglob("*.tsx")):
        if "node_modules" in path.parts or "__tests__" in path.parts:
            continue
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)
        if re.search(r"\.step\s*===?\s*['\"]|\bstep\s*===\s*['\"]", code):
            offenders.append(str(path.relative_to(_UI)))
    assert not offenders, (
        "a component branches on the diagnostic step: " + ", ".join(offenders)
    )
