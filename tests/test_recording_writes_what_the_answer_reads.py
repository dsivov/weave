"""Recording a review or a learning writes the node the answer reads (D-043, W23).

**The measurement that produced this file.** On a workspace created from nothing
— the state every reader of the guide is in — seeding 7 learnings and 6 reviews
left `/ask/learnings` answering **0**, while the graph held 21 generic `ENTITY`
nodes and not one `Insight` or `Review`. `record_learning` wrote a *decision
trace*; `ask_learnings` seeds on `entity_type in (Review, Insight)`; the typed
nodes were never created at all. `weave migrate reviews` then produced all 13 and
the answer worked.

**Why two reviews and a browser pass missed it.** Every measurement anyone took
was against the demo tenant, which had been migrated historically and therefore
already knew the answer. A tenant that has been repaired cannot demonstrate that
a fresh one works — so the gate here is a workspace built inside the test, with
no migration run, and the assertion is that the question answers anyway.

**Against a real storage adapter, not a fake.** `NetworkXStorage` is the
file-based path (A4) and `emit_decision_trace` is the real method bound to a real
graph, because two of the properties below — that a governed node survives a
later generic write, and that the migration finds nothing left to do — are
properties of that code, and a fake would only prove the fake agrees with itself.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

import pytest

from weave.model.answers import ask_learnings
from weave.model.insights import insight_node_id, review_node_id
from weave.model.migrate_reviews import migrate_workspace
from weave.team import preset
from weave.team.coordinator import WeaveCoordinator, WeaveError
from weave.team.store import InMemoryWeaveTaskStore
from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService

pytestmark = pytest.mark.offline

WORKSPACE = "w"


class _Rag:
    """A real graph plus the real `emit_decision_trace`.

    The decision *indices* are stubbed out and nothing else is. They are derived
    projections of the edge — rebuildable with `reindex_decisions()` — and
    standing them up needs vector stores and an embedding function, neither of
    which any assertion here touches. The graph write, the endpoint-node
    creation and the merge are the code under test and they are genuine.
    """

    def __init__(self, store):
        from weave_core.graph.quadruple import WeaveGraph

        self._impl = WeaveGraph.__new__(WeaveGraph)
        self._impl.chunk_entity_relation_graph = store
        self._impl.workspace = ""
        self.chunk_entity_relation_graph = store

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        async def _noop(*a, **k):
            return None

        self._impl._index_decision = _noop
        self._impl._persist_decision_indices = _noop
        return await self._impl.emit_decision_trace(
            src, tgt, relation_type, rc, upsert=upsert)

    async def find_precedents(self, query, top_k=10, min_confidence=0.0):
        return []


@pytest.fixture
async def graph():
    from weave_core.graph.storage.files import NetworkXStorage
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)
    store = NetworkXStorage(
        namespace="chunk_entity_relation",
        workspace="",
        global_config={"working_dir": tempfile.mkdtemp(prefix="p101-"),
                       "embedding_batch_num": 8},
        embedding_func=None,
    )
    await store.initialize()
    return store


@pytest.fixture
def coordinator(graph):
    """A workspace created from nothing. No seed, no migration, no history."""
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    lifecycle.save(WORKSPACE, preset.load_part("lifecycle"))
    rag = _Rag(graph)
    return WeaveCoordinator(
        InMemoryWeaveTaskStore(), lifecycle_service=lifecycle,
        rag_resolver=lambda ws: rag)


async def _a_reviewed_task(c, task_id="T-1"):
    """Drive one task far enough to have a review recorded against it."""
    c.create_task(WORKSPACE, task_id, title="wire the guard", touches=[task_id])
    await c.claim(WORKSPACE, task_id, worker="dev-1", role="developer")
    await c.open_pull_request(WORKSPACE, task_id, branch=f"feat/{task_id}",
                              url="http://pr/1", role="developer")
    return task_id


# ── the gate ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_clean_workspace_answers_what_did_we_learn(coordinator, graph):
    """**The gate (M10.1), and the whole of it.**

    Record, then ask. No migration between them — that is the entire point, and
    anything weaker restates what the migration was already known to do.
    """
    task = await _a_reviewed_task(coordinator)
    await coordinator.record_review(
        WORKSPACE, task, verdict="approve", by="architect",
        notes="clean: no architecture-sensitive modules")
    await coordinator.record_learning(
        WORKSPACE, insight="a guard's reach is not its rule", task_id=task,
        by="developer")

    answer = await ask_learnings(graph)

    assert answer["count"] == 2, (
        "a workspace that was told a review and a learning answers *what did we "
        f"learn* with {answer['count']} — this is W23, which is the defect this "
        "phase exists to close"
    )
    types = sorted(n["type"] for n in answer["nodes"])
    assert types == ["Insight", "Review"]

    # And the answer is readable, not a wall of ids (U3): the statement and the
    # review's notes are what a person came for.
    labels = sorted(n["label"] for n in answer["nodes"])
    assert labels == ["a guard's reach is not its rule",
                      "clean: no architecture-sensitive modules"]


@pytest.mark.asyncio
async def test_the_migration_finds_nothing_left_to_move(coordinator, graph):
    """**One builder, two callers — asserted by observation rather than by
    reading both.**

    If the live path and the migration ever derived a different id, or built a
    different node for the same entry, this would create a second node. Zero is
    the only number that means they cannot differ.
    """
    task = await _a_reviewed_task(coordinator)
    await coordinator.record_review(WORKSPACE, task, verdict="flag",
                                    by="review-agent", notes="touches auth")
    await coordinator.record_learning(WORKSPACE, insight="lesson one",
                                      task_id=task, by="developer")

    report = await migrate_workspace(WORKSPACE, coordinator.store, graph)

    assert report["nodes_expected"] == 2
    assert report["nodes_created"] == 0, (
        "the migration created a node for an entry that was already recorded "
        "live — the two paths derive different ids or build different nodes, "
        "which is exactly what sharing the builder is for"
    )
    assert report["nodes_already_present"] == 2


@pytest.mark.asyncio
async def test_the_ids_are_the_positions_the_migration_would_compute(coordinator, graph):
    """The mechanism behind the zero above: position in an append-only list."""
    task = await _a_reviewed_task(coordinator)
    for i in range(2):
        await coordinator.record_learning(WORKSPACE, insight=f"lesson {i}",
                                          task_id=task, by="developer")

    assert await graph.get_node(insight_node_id(task, 0)) is not None
    assert await graph.get_node(insight_node_id(task, 1)) is not None


@pytest.mark.asyncio
async def test_the_same_lesson_twice_is_two_nodes(coordinator, graph):
    """Position, not a content hash — for a task learning.

    Two identical insights on one task are two facts about how often the team
    learned that thing, and R25 counts the move by count as well as by content.
    """
    task = await _a_reviewed_task(coordinator)
    for _ in range(2):
        await coordinator.record_learning(WORKSPACE, insight="the same lesson",
                                          task_id=task, by="developer")

    answer = await ask_learnings(graph)
    assert answer["count"] == 2


@pytest.mark.asyncio
async def test_a_project_level_learning_is_answerable_too(coordinator, graph):
    """`POST /weave/learnings` with no `task` is a reachable path — `task` is
    `Optional` on the request model — and a learning that no question can find
    is W23 on a smaller surface rather than a different defect."""
    await coordinator.record_learning(
        WORKSPACE, insight="the hub never dials out", by="architect")

    answer = await ask_learnings(graph)
    assert answer["count"] == 1
    assert answer["nodes"][0]["label"] == "the hub never dials out"


@pytest.mark.asyncio
async def test_the_scoped_question_reaches_that_tasks_learnings(coordinator, graph):
    """*What did we learn **here***.

    The walk starts at the task and admits only `Review`/`Insight`, so it cannot
    hop through the `PR:` node the audit edge used to run from. This is why the
    live edge was moved onto the task, where the migration already put it: the
    two must answer the scoped question the same way.
    """
    task = await _a_reviewed_task(coordinator)
    await coordinator.record_review(WORKSPACE, task, verdict="approve",
                                    by="architect", notes="looks right")
    await coordinator.record_learning(WORKSPACE, insight="scoped lesson",
                                      task_id=task, by="developer")

    answer = await ask_learnings(graph, scope=task)
    assert sorted(n["label"] for n in answer["nodes"]) == ["looks right", "scoped lesson"]


# ── the half without which the first half is temporary (W17) ─────────────────


@pytest.mark.asyncio
async def test_a_typed_node_survives_a_later_generic_write(coordinator, graph):
    """`emit_decision_trace` may not retype a governed node.

    **This property already held, and it is pinned here because nothing was
    watching it.** The guard is in `emit_decision_trace` — it creates a missing
    endpoint as a generic `ENTITY` and skips one that exists — and it is what
    makes creating the node typed durable rather than true until the next audit
    edge touches it.

    W17 was recorded as *a generic upsert silently retypes a governed node*, and
    that reading does not survive the artefact: the node it was seen on,
    `review:T-P0-FORK`, is not an id the migration ever writes — the migration
    writes `review:T-P0-FORK:0`. It was never a governed node that got retyped;
    it was the audit edge's target, generic from birth, because recording
    pointed its edge at an id no typed node had.
    """
    task = await _a_reviewed_task(coordinator)
    await coordinator.record_review(WORKSPACE, task, verdict="approve",
                                    by="architect", notes="the notes")
    review_id = review_node_id(task, 0)
    assert (await graph.get_node(review_id))["entity_type"] == "Review"

    # A second review, and a third: every one of these writes an audit edge
    # whose endpoints pass through the same creation path.
    await coordinator.record_review(WORKSPACE, task, verdict="flag",
                                    by="review-agent", notes="second look")
    await coordinator.record_learning(WORKSPACE, insight="after the fact",
                                      task_id=task, by="developer")

    node = await graph.get_node(review_id)
    assert node["entity_type"] == "Review", (
        "a later generic write retyped a governed node — the answer surface "
        "seeds on the type, so this empties the answer with no error at all"
    )
    assert node["summary"] == "the notes", "the node's content was clobbered"
    assert (await ask_learnings(graph))["count"] == 3


@pytest.mark.asyncio
async def test_a_failed_node_write_is_not_swallowed(coordinator, graph):
    """Must-succeed, and the reason it differs from `_reflect_node`.

    A `Commit` node is an audit reflection of a task the store already holds, so
    losing it costs an audit row. An `Insight` node is the only record the
    question reads, so a swallowed failure is a learning that was accepted and
    cannot be found — W23 arriving one write at a time.

    **Only the insight's own write is refused.** The first version of this test
    failed every `upsert_node`, and a control showed it then passed with the
    guarantee removed: `emit_decision_trace` creates missing edge endpoints
    through the same call, so the error it was catching came from the audit
    path, not from the write under test. A test that passes for a reason it did
    not name is a test that will keep passing once that reason changes.
    """
    task = await _a_reviewed_task(coordinator)
    target = insight_node_id(task, 0)
    real = graph.upsert_node

    async def _refuse_only_the_insight(node_id, node_data):
        if node_id == target:
            raise RuntimeError("graph backend down")
        return await real(node_id, node_data)

    graph.upsert_node = _refuse_only_the_insight
    with pytest.raises(RuntimeError):
        await coordinator.record_learning(WORKSPACE, insight="lost", task_id=task,
                                          by="developer")


@pytest.mark.asyncio
async def test_a_workspace_with_no_graph_is_refused_not_ignored(coordinator):
    """A coordinator with no rag resolver cannot record a learning silently."""
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    lifecycle.save(WORKSPACE, preset.load_part("lifecycle"))
    c = WeaveCoordinator(InMemoryWeaveTaskStore(), lifecycle_service=lifecycle)
    with pytest.raises(WeaveError):
        await c.record_learning(WORKSPACE, insight="nowhere to put this")


# ── one builder, checked as a class rather than as an instance ───────────────


_REPO = pathlib.Path(__file__).resolve().parent.parent


def test_only_one_module_builds_a_review_or_insight_node():
    """The rule, not the two files it currently holds.

    A third place constructing `entity_type: "Review"` is a second builder
    whether or not it is called a migration, and it would drift from this one
    the first time either changed. `weave/model/insights.py` is the builder;
    everything else calls it.
    """
    offenders = []
    pattern = re.compile(r"""["']entity_type["']\s*:\s*["'](Review|Insight)["']""")
    for path in sorted((_REPO / "weave").rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "insights.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_REPO)))
    assert not offenders, (
        "these build a Review or Insight node outside weave/model/insights.py:\n  "
        + "\n  ".join(offenders)
        + "\n\n  One builder, two callers (D-043) — a live node and a migrated "
        "node that differ would show up as 'the answer is right on the old "
        "tenant and wrong on the new one'."
    )


def test_the_recording_path_calls_the_builder():
    """The other direction: the coordinator must not have quietly stopped."""
    code = (_REPO / "weave" / "team" / "coordinator.py").read_text(encoding="utf-8")
    for call in ("review_node(", "insight_node(", "project_insight_node("):
        assert call in code, f"the recording path no longer calls {call}"
