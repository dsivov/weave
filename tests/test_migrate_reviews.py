"""Task `reviews`/`learnings` become `Review`/`Insight` nodes, once (R25).

The gate is specific: **100% moved, asserted by count and by content, and the
second run is a no-op.** Both halves matter and they fail differently — a
migration that moves 90% silently is data loss, and one that is not idempotent
turns a retried deploy into duplicate history.

The source fields are deliberately *not* removed here. R25 removes them only
after M2 is signed off, and a migration that deletes its own input leaves nothing
to verify against.
"""

from __future__ import annotations

import pytest

from weave.model.migrate_reviews import (
    insight_node_id,
    migrate_workspace,
    review_node_id,
    verify_workspace,
)
from weave.team.store import InMemoryWeaveTaskStore, WeaveTask

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"


class FakeGraph:
    """Nodes and edges, with the four calls the migration uses."""

    def __init__(self) -> None:
        self.nodes: dict = {}
        self.edges: list = []
        self.upserts = 0

    async def has_node(self, node_id):
        return node_id in self.nodes

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def upsert_node(self, node_id, data):
        self.upserts += 1
        self.nodes[node_id] = dict(data)

    async def upsert_edge(self, src, tgt, data):
        self.edges.append((src, tgt, dict(data)))

    async def get_all_labels(self):
        return sorted(self.nodes)


@pytest.fixture
def store():
    store = InMemoryWeaveTaskStore()
    store.save(WORKSPACE, WeaveTask(
        id="TASK-1", title="Wire the guard",
        reviews=[
            {"verdict": "flag", "by": "review-agent",
             "notes": "touches architecture-sensitive modules"},
            {"verdict": "approve", "by": "architect", "notes": ""},
        ],
        learnings=[
            "a guard in an adapter protects only that adapter's callers",
            "the workspace header had two spellings",
        ],
    ))
    store.save(WORKSPACE, WeaveTask(
        id="TASK-2", title="Nothing recorded yet",
    ))
    store.save(WORKSPACE, WeaveTask(
        id="TASK-3", title="Learned but never reviewed",
        learnings=["a locator without a rev resolves against a moving target"],
    ))
    # A different workspace, which must not be touched.
    store.save("beta", WeaveTask(
        id="TASK-9", title="Someone else's",
        reviews=[{"verdict": "approve", "by": "architect", "notes": "theirs"}],
        learnings=["theirs too"],
    ))
    return store


@pytest.fixture
def graph():
    return FakeGraph()


# ── 100% moved, by count ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_entry_becomes_a_node(store, graph):
    report = await migrate_workspace(WORKSPACE, store, graph)

    assert report["reviews_found"] == 2
    assert report["learnings_found"] == 3
    assert report["nodes_expected"] == 5
    assert report["nodes_created"] == 5
    assert report["nodes_already_present"] == 0
    assert len(graph.nodes) == 5


@pytest.mark.asyncio
async def test_two_identical_learnings_become_two_nodes(graph):
    """Ids are derived from position, not content. A content hash would collapse
    these into one and quietly fail the count assertion — and two identical
    insights recorded twice are two facts about how often it was learned."""
    store = InMemoryWeaveTaskStore()
    store.save(WORKSPACE, WeaveTask(id="T", learnings=["same", "same"]))

    report = await migrate_workspace(WORKSPACE, store, graph)

    assert report["nodes_created"] == 2
    assert len(graph.nodes) == 2


# ── 100% moved, by content ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_content_survives_the_move(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)

    first = graph.nodes[review_node_id("TASK-1", 0)]
    assert first["entity_type"] == "Review"
    assert first["verdict"] == "flag"
    assert first["reviewer"] == "review-agent"
    assert first["summary"] == "touches architecture-sensitive modules"
    assert first["migrated_from"] == "task:TASK-1.reviews[0]"


@pytest.mark.asyncio
async def test_learning_content_survives_the_move(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)

    node = graph.nodes[insight_node_id("TASK-1", 0)]
    assert node["entity_type"] == "Insight"
    assert node["statement"] == (
        "a guard in an adapter protects only that adapter's callers"
    )


@pytest.mark.asyncio
async def test_verify_confirms_the_move_independently(store, graph):
    """`verify_workspace` re-reads both sides. A migration that reports its own
    success is only as trustworthy as the path that just ran."""
    await migrate_workspace(WORKSPACE, store, graph)

    result = await verify_workspace(WORKSPACE, store, graph)
    assert result["complete"] is True
    assert result["checked"] == 5
    assert result["missing"] == [] and result["mismatched"] == []


@pytest.mark.asyncio
async def test_verify_catches_a_node_whose_content_drifted(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)
    graph.nodes[insight_node_id("TASK-1", 0)]["statement"] = "something else"

    result = await verify_workspace(WORKSPACE, store, graph)
    assert result["complete"] is False
    assert insight_node_id("TASK-1", 0) in result["mismatched"]


@pytest.mark.asyncio
async def test_verify_catches_a_missing_node(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)
    del graph.nodes[review_node_id("TASK-1", 1)]

    result = await verify_workspace(WORKSPACE, store, graph)
    assert result["complete"] is False
    assert review_node_id("TASK-1", 1) in result["missing"]


# ── the second run is a no-op ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_run_creates_nothing(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)
    before = dict(graph.nodes)

    second = await migrate_workspace(WORKSPACE, store, graph)

    assert second["nodes_created"] == 0
    assert second["nodes_already_present"] == 5
    assert graph.nodes == before, "a second run changed the graph"


@pytest.mark.asyncio
async def test_a_run_after_new_entries_moves_only_the_new_ones(store, graph):
    """The realistic case: the migration runs, work continues, it runs again."""
    await migrate_workspace(WORKSPACE, store, graph)

    task = store.get(WORKSPACE, "TASK-1")
    task.learnings.append("append-only lists make position a stable id")
    store.save(WORKSPACE, task)

    second = await migrate_workspace(WORKSPACE, store, graph)

    assert second["nodes_created"] == 1
    assert second["nodes_already_present"] == 5
    assert graph.nodes[insight_node_id("TASK-1", 2)]["statement"].startswith(
        "append-only"
    )


# ── it stays inside the workspace, and leaves the source alone ───────────────


@pytest.mark.asyncio
async def test_another_workspace_is_not_touched(store, graph):
    await migrate_workspace(WORKSPACE, store, graph)

    assert not any("TASK-9" in node_id for node_id in graph.nodes)
    assert store.get("beta", "TASK-9").reviews, "another workspace's data changed"


@pytest.mark.asyncio
async def test_the_source_fields_are_left_in_place(store, graph):
    """R25 removes them only after M2 is signed off. A migration that deletes its
    own input leaves nothing to verify against."""
    await migrate_workspace(WORKSPACE, store, graph)

    task = store.get(WORKSPACE, "TASK-1")
    assert len(task.reviews) == 2 and len(task.learnings) == 2


@pytest.mark.asyncio
async def test_a_dry_run_reports_without_writing(store, graph):
    report = await migrate_workspace(WORKSPACE, store, graph, dry_run=True)

    assert report["nodes_expected"] == 5
    assert report["dry_run"] is True
    assert graph.nodes == {} and graph.upserts == 0


# ── the new nodes are reachable from the task they came from ─────────────────


@pytest.mark.asyncio
async def test_nodes_are_linked_to_their_task_when_the_task_is_in_the_graph(
    store, graph
):
    await graph.upsert_node("TASK-1", {"entity_id": "TASK-1", "entity_type": "Task"})
    await migrate_workspace(WORKSPACE, store, graph)

    linked = {(src, tgt) for src, tgt, _ in graph.edges}
    assert ("TASK-1", review_node_id("TASK-1", 0)) in linked
    assert ("TASK-1", insight_node_id("TASK-1", 0)) in linked


@pytest.mark.asyncio
async def test_no_edge_is_written_to_a_task_the_graph_does_not_hold(store, graph):
    """The task store is the source of truth for tasks; the graph holds only the
    ones reflected onto it. A dangling edge would be worse than a node reachable
    only by type."""
    await migrate_workspace(WORKSPACE, store, graph)

    assert graph.edges == []
    assert len(graph.nodes) == 5, "the nodes themselves must still be created"


@pytest.mark.asyncio
async def test_a_learning_is_attached_to_its_task_not_to_an_invented_review(
    store, graph
):
    """`record_learning` writes against a task, not against a review. Hanging the
    insight off a fabricated review would invent a relationship that never
    existed — TASK-3 has a learning and no review at all."""
    await graph.upsert_node("TASK-3", {"entity_id": "TASK-3", "entity_type": "Task"})
    await migrate_workspace(WORKSPACE, store, graph)

    relations = {(src, tgt, data["relation"]) for src, tgt, data in graph.edges}
    assert ("TASK-3", insight_node_id("TASK-3", 0), "yielded") in relations
    assert not any(src.startswith("review:") for src, _, _ in graph.edges)
