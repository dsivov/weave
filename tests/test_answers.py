"""The four questions, each answered by one traversal returning nodes (R20).

*What changed · why · what does it do · what did we learn.* The M2 gate asks for
nodes rather than a text blob, because a node can be cited, followed, and
resolved back to a document through its locator.

These run against a small in-memory graph implementing the slice of the graph
port the traversals use. That is deliberate: the property under test is the
*shape of each answer* — which nodes it admits and which it refuses — and a real
storage backend would add setup without adding evidence. The tenant boundary and
the storage paths are asserted where they belong, in
`test_project_layout_tenancy.py` and `test_storage_paths.py`.
"""

from __future__ import annotations

import pytest

from weave.model.answers import (
    ANSWER_FUNCTIONS,
    ask_changes,
    ask_features,
    ask_learnings,
    ask_why,
)


class FakeGraph:
    """The slice of the graph port the traversals use: nodes, edges, labels."""

    def __init__(self, nodes: dict, edges: list) -> None:
        self._nodes = {k: {"entity_id": k, **v} for k, v in nodes.items()}
        self._edges = list(edges)

    async def get_node(self, node_id):
        return self._nodes.get(node_id)

    async def get_node_edges(self, node_id):
        return [(s, t) for s, t in self._edges if s == node_id or t == node_id]

    async def get_all_labels(self):
        return sorted(self._nodes)


@pytest.fixture
def graph() -> FakeGraph:
    """One feature, delivered, reviewed, and learned from — plus a second,
    unrelated feature that must never appear in the first one's answers."""
    nodes = {
        "FEAT-1": {"entity_type": "Feature", "title": "Governed actions",
                   "locator_repo": "weave", "locator_path": "docs/rfc.md",
                   "locator_rev": "abc123"},
        "CR-1": {"entity_type": "ChangeRequest", "title": "Add RBAC"},
        "TASK-1": {"entity_type": "Task", "title": "Wire the guard"},
        "abc1234": {"entity_type": "Commit", "sha": "abc1234", "subject": "guard"},
        "PR:TASK-1": {"entity_type": "PullRequest", "title": "Wire the guard"},
        "RUN-1": {"entity_type": "IntegrationRun", "status": "passed"},
        "ADR-1": {"entity_type": "ArchitectureDecisionRecord",
                  "title": "Deny by default"},
        "REVIEW-1": {"entity_type": "Review", "verdict": "approved",
                     "reviewer": "architect"},
        "INSIGHT-1": {"entity_type": "Insight",
                      "statement": "a guard in an adapter is not a guard"},
        "MOD-1": {"entity_type": "Module", "path": "weave/governance"},
        "PRD-1": {"entity_type": "PRD", "title": "Governance"},
        "DIA-1": {"entity_type": "Diagram", "title": "The guard chain"},
        "ROLE-1": {"entity_type": "Role", "description": "architect"},

        # A second, disconnected feature.
        "FEAT-2": {"entity_type": "Feature", "title": "Live board"},
        "CR-2": {"entity_type": "ChangeRequest", "title": "SSE"},
    }
    edges = [
        ("FEAT-1", "CR-1"), ("CR-1", "TASK-1"), ("TASK-1", "abc1234"),
        ("TASK-1", "PR:TASK-1"), ("PR:TASK-1", "RUN-1"),
        ("CR-1", "ADR-1"), ("ADR-1", "ROLE-1"),
        ("TASK-1", "REVIEW-1"), ("REVIEW-1", "INSIGHT-1"),
        ("FEAT-1", "MOD-1"), ("FEAT-1", "PRD-1"), ("FEAT-1", "DIA-1"),
        ("FEAT-2", "CR-2"),
    ]
    return FakeGraph(nodes, edges)


def _ids(answer) -> set:
    return {n["id"] for n in answer["nodes"]}


def _types(answer) -> set:
    return {n["type"] for n in answer["nodes"]}


# ── each question returns nodes, not prose ───────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_every_answer_is_a_set_of_nodes(graph):
    """The gate criterion, asserted for all four at once."""
    answers = [
        await ask_changes(graph),
        await ask_why(graph, node="CR-1"),
        await ask_features(graph),
        await ask_learnings(graph),
    ]
    for answer in answers:
        assert answer["nodes"], f"{answer['question']} returned nothing"
        assert answer["count"] == len(answer["nodes"])
        for node in answer["nodes"]:
            assert node["id"] and node["type"], "a node without an id or a type"
            assert "locator" in node, "a node that cannot be resolved back"


# ── what changed ─────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_changes_walks_the_whole_delivery_chain(graph):
    answer = await ask_changes(graph, feature="FEAT-1")
    assert {"CR-1", "TASK-1", "abc1234", "PR:TASK-1", "RUN-1"} <= _ids(answer)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_changes_does_not_wander_out_of_the_chain(graph):
    """`ADR-1` and `REVIEW-1` are one hop from `TASK-1`/`CR-1`. They are other
    questions' answers, and a traversal that returns everything reachable is not
    an answer — it is a graph dump."""
    answer = await ask_changes(graph, feature="FEAT-1")
    assert "ADR-1" not in _ids(answer)
    assert "REVIEW-1" not in _ids(answer)
    assert "ROLE-1" not in _ids(answer)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_an_unrelated_feature_does_not_leak_into_the_answer(graph):
    answer = await ask_changes(graph, feature="FEAT-1")
    assert "FEAT-2" not in _ids(answer) and "CR-2" not in _ids(answer)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_changes_without_an_anchor_covers_every_change_request(graph):
    answer = await ask_changes(graph)
    assert {"CR-1", "CR-2"} <= _ids(answer)


# ── why ──────────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_why_reaches_the_decision_and_counts_it(graph):
    answer = await ask_why(graph, node="TASK-1")
    assert "ADR-1" in _ids(answer)
    assert answer["decisions"] == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_why_needs_something_to_be_about(graph):
    """Without an anchor this is "list every decision", which is a different
    question."""
    with pytest.raises(ValueError):
        await ask_why(graph, node="")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_why_includes_its_anchor_even_when_the_anchor_is_not_a_why_type(graph):
    """A seed is admitted regardless of type: the question is *about* it."""
    answer = await ask_why(graph, node="FEAT-1")
    assert "FEAT-1" in _ids(answer)


# ── what does it do ──────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_features_returns_what_describes_each_capability(graph):
    answer = await ask_features(graph, feature="FEAT-1")
    assert {"MOD-1", "PRD-1", "DIA-1"} <= _ids(answer)
    assert _types(answer) <= {"Feature", "Module", "PRD", "RFC", "Diagram"}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_features_without_an_anchor_lists_every_feature(graph):
    answer = await ask_features(graph)
    assert {"FEAT-1", "FEAT-2"} <= _ids(answer)


# ── what did we learn ────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_learnings_returns_reviews_and_insights(graph):
    answer = await ask_learnings(graph)
    assert {"REVIEW-1", "INSIGHT-1"} == _ids(answer)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_learnings_scoped_to_a_node_excludes_the_node_itself(graph):
    """"What did we learn about TASK-1" should not answer "TASK-1"."""
    answer = await ask_learnings(graph, scope="TASK-1")
    assert "TASK-1" not in _ids(answer)
    assert {"REVIEW-1", "INSIGHT-1"} <= _ids(answer)


# ── the locator comes back with the node ─────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_node_carries_its_locator_as_a_structure(graph):
    answer = await ask_features(graph, feature="FEAT-1")
    feature = next(n for n in answer["nodes"] if n["id"] == "FEAT-1")
    assert feature["locator"] == {
        "repo": "weave", "path": "docs/rfc.md", "rev": "abc123",
    }


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_node_with_no_locator_reports_none_rather_than_omitting_it(graph):
    """`None` is an answer the resolver check can count. A missing key is one it
    would have to guess about."""
    answer = await ask_learnings(graph)
    assert all(n["locator"] is None for n in answer["nodes"])


@pytest.mark.offline
@pytest.mark.asyncio
async def test_one_malformed_locator_does_not_sink_the_whole_answer():
    """A partial locator is a real defect, and `scripts/check_locators.py` is
    what finds it — but it must not make an otherwise good answer unavailable."""
    graph = FakeGraph(
        {
            "REVIEW-1": {"entity_type": "Review", "verdict": "approved",
                         "locator_repo": "weave", "locator_path": "r.md"},  # no rev
            "INSIGHT-1": {"entity_type": "Insight", "statement": "fine"},
        },
        [("REVIEW-1", "INSIGHT-1")],
    )
    answer = await ask_learnings(graph)

    broken = next(n for n in answer["nodes"] if n["id"] == "REVIEW-1")
    assert broken["locator"] is None and broken["locator_error"]
    assert "INSIGHT-1" in _ids(answer), "one bad node hid the rest of the answer"


# ── one registry, so a fifth question cannot reach one surface only ──────────


@pytest.mark.offline
def test_the_four_questions_are_declared_in_one_place():
    """The routers and the MCP tools both iterate `ANSWER_FUNCTIONS`. A question
    added to one surface and forgotten on the other is the A9 failure this
    registry exists to make impossible."""
    assert set(ANSWER_FUNCTIONS) == {"changes", "why", "features", "learnings"}
    assert ANSWER_FUNCTIONS["changes"] is ask_changes
    assert ANSWER_FUNCTIONS["why"] is ask_why
    assert ANSWER_FUNCTIONS["features"] is ask_features
    assert ANSWER_FUNCTIONS["learnings"] is ask_learnings
