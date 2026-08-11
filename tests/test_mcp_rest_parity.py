"""The same question, asked via MCP and via REST, returns the same node set (R26, A9).

Two answer surfaces that disagree are worse than one, because neither can be
trusted and nobody knows which is wrong. So the contract is node-set equality,
and it is asserted **by calling both surfaces** — not by checking they import the
same symbol.

That distinction is the whole point of this file. A shared call site is the
*implementation* of parity; equal answers are the *contract*. A test that asserts
`routers.ask` and `mcp` reference the same function would keep passing if one
adapter started filtering the result, unwrapping it, renaming a field, or
silently dropping nodes without locators — every one of which breaks R26 while
leaving the shared symbol intact. So both surfaces are actually invoked, over
their real transports where that is possible, and their outputs compared.

The four questions are driven from `ANSWER_FUNCTIONS`, so a fifth question that
reaches one surface and not the other fails here rather than shipping.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave.model.answers import ANSWER_FUNCTIONS
from weave.server.routers.ask import ANCHORS, create_ask_routes

pytestmark = pytest.mark.offline


class FakeGraph:
    def __init__(self, nodes: dict, edges: list) -> None:
        self._nodes = {k: {"entity_id": k, **v} for k, v in nodes.items()}
        self._edges = list(edges)

    async def get_node(self, node_id):
        return self._nodes.get(node_id)

    async def get_node_edges(self, node_id):
        return [(s, t) for s, t in self._edges if s == node_id or t == node_id]

    async def get_all_labels(self):
        return sorted(self._nodes)


def _fake_rag_class():
    """A `WeaveGraph` that carries a graph and nothing else.

    The MCP tools gate on `isinstance(rag, WeaveGraph)` — Weave mode is a real
    precondition and stubbing past it would test a configuration nobody runs. So
    this subclasses the real type and skips its (heavy, storage-opening)
    `__init__` rather than faking the check away.
    """
    from weave_core.graph.quadruple import WeaveGraph

    class FakeRag(WeaveGraph):
        rules_gate = object()

        def __init__(self, graph):  # noqa: D107 - see the factory docstring
            self.chunk_entity_relation_graph = graph

    return FakeRag


FakeRag = _fake_rag_class()


NODES = {
    "FEAT-1": {"entity_type": "Feature", "title": "Governed actions",
               "locator_repo": "weave", "locator_path": "docs/rfc.md",
               "locator_rev": "abc123"},
    "CR-1": {"entity_type": "ChangeRequest", "title": "Add RBAC"},
    "TASK-1": {"entity_type": "Task", "title": "Wire the guard"},
    "abc1234": {"entity_type": "Commit", "sha": "abc1234", "subject": "guard"},
    "PR:TASK-1": {"entity_type": "PullRequest", "title": "Wire the guard"},
    "RUN-1": {"entity_type": "IntegrationRun", "status": "passed"},
    "ADR-1": {"entity_type": "ArchitectureDecisionRecord", "title": "Deny by default"},
    "REVIEW-1": {"entity_type": "Review", "verdict": "approved", "reviewer": "arch"},
    "INSIGHT-1": {"entity_type": "Insight", "statement": "adapters are not guards"},
    "MOD-1": {"entity_type": "Module", "path": "weave/governance"},
    "PRD-1": {"entity_type": "PRD", "title": "Governance"},
    "DIA-1": {"entity_type": "Diagram", "title": "The guard chain"},
}
EDGES = [
    ("FEAT-1", "CR-1"), ("CR-1", "TASK-1"), ("TASK-1", "abc1234"),
    ("TASK-1", "PR:TASK-1"), ("PR:TASK-1", "RUN-1"), ("CR-1", "ADR-1"),
    ("TASK-1", "REVIEW-1"), ("REVIEW-1", "INSIGHT-1"),
    ("FEAT-1", "MOD-1"), ("FEAT-1", "PRD-1"), ("FEAT-1", "DIA-1"),
]

#: The anchor each question is asked with here. `why` must have one.
ASKED_WITH = {
    "changes": {"feature": "FEAT-1"},
    "why": {"node": "TASK-1"},
    "features": {"feature": "FEAT-1"},
    "learnings": {"scope": "TASK-1"},
}


@pytest.fixture
def rag() -> FakeRag:
    return FakeRag(FakeGraph(NODES, EDGES))


@pytest.fixture
def rest(rag) -> TestClient:
    app = FastAPI()
    app.include_router(create_ask_routes(rag))
    return TestClient(app)


async def _via_mcp(rag, question: str, params: dict) -> dict:
    """Call the MCP tool body for *question*.

    The tools are closures created inside `create_mcp_server`, so they are
    reached through the registered tool rather than by importing a function —
    which is the point: this exercises what an agent would actually invoke.
    """
    from weave.server.mcp import create_mcp_server

    mcp, _ = create_mcp_server(rag)
    return await mcp.call_tool(f"ask_{question}", params)


def _node_ids(answer: dict) -> set:
    return {n["id"] for n in answer["nodes"]}


def _unwrap(result) -> dict:
    """The payload an agent actually receives from an MCP tool call.

    `call_tool` hands back content blocks — the tool's dict serialised as JSON
    text — and, on versions that convert results, a `(blocks, structured)`
    pair. Parsing the block rather than reaching for the Python object is
    deliberate: it is the JSON round-trip an agent performs, so a field that
    fails to serialise shows up here instead of at an agent's next call.
    """
    if isinstance(result, tuple) and len(result) == 2:
        blocks, structured = result
        if isinstance(structured, dict):
            return structured.get("result", structured)
        result = blocks
    if isinstance(result, dict):
        return result.get("result", result)
    if isinstance(result, (list, tuple)) and result:
        text = getattr(result[0], "text", None)
        if text:
            return json.loads(text)
    return {}


# ── the contract: equal node sets, both surfaces actually called ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("question", sorted(ANSWER_FUNCTIONS))
async def test_the_same_question_returns_the_same_node_set(question, rag, rest):
    params = ASKED_WITH[question]
    param_name = ANCHORS[question][0]

    over_rest = rest.get(f"/ask/{question}", params={param_name: params[param_name]})
    assert over_rest.status_code == 200, over_rest.text
    rest_answer = over_rest.json()

    mcp_answer = _unwrap(await _via_mcp(rag, question, params))

    assert _node_ids(rest_answer), f"/ask/{question} answered nothing to compare"
    assert _node_ids(mcp_answer) == _node_ids(rest_answer), (
        f"MCP and REST disagree on '{question}': "
        f"only in MCP {_node_ids(mcp_answer) - _node_ids(rest_answer)}, "
        f"only in REST {_node_ids(rest_answer) - _node_ids(mcp_answer)}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("question", sorted(ANSWER_FUNCTIONS))
async def test_the_nodes_themselves_match_not_only_their_ids(question, rag, rest):
    """Equal ids with different bodies would still be two answers. The locator
    especially: a surface that dropped it would leave an agent with a citation
    it cannot follow."""
    params = ASKED_WITH[question]
    param_name = ANCHORS[question][0]

    rest_answer = rest.get(
        f"/ask/{question}", params={param_name: params[param_name]}
    ).json()
    mcp_answer = _unwrap(await _via_mcp(rag, question, params))

    by_id_rest = {n["id"]: n for n in rest_answer["nodes"]}
    by_id_mcp = {n["id"]: n for n in mcp_answer["nodes"]}

    for node_id, rest_node in by_id_rest.items():
        mcp_node = by_id_mcp[node_id]
        assert mcp_node["type"] == rest_node["type"]
        assert mcp_node.get("locator") == rest_node.get("locator"), (
            f"the locator for {node_id} differs between surfaces"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("question", sorted(ANSWER_FUNCTIONS))
async def test_the_unanchored_forms_agree_too(question, rag, rest):
    """`why` is the exception: it requires an anchor on both surfaces, and the
    refusal is part of the parity."""
    param_name, required = ANCHORS[question]
    if required:
        assert rest.get(f"/ask/{question}").status_code == 422
        with pytest.raises(Exception):
            await _via_mcp(rag, question, {})
        return

    rest_answer = rest.get(f"/ask/{question}").json()
    mcp_answer = _unwrap(await _via_mcp(rag, question, {}))
    assert _node_ids(mcp_answer) == _node_ids(rest_answer)


# ── neither surface may know a question the other does not ───────────────────


@pytest.mark.asyncio
async def test_both_surfaces_expose_exactly_the_four_questions(rag):
    """The A9 failure this file exists to prevent: a question added to one
    adapter and forgotten on the other. Both are generated from
    `ANSWER_FUNCTIONS`, and this asserts the generation actually happened rather
    than trusting that it would."""
    from weave.server.mcp import create_mcp_server

    app = FastAPI()
    app.include_router(create_ask_routes(rag))
    # Read the generated OpenAPI document rather than `app.routes`: it is the
    # contract a client is entitled to rely on, and it survives FastAPI wrapping
    # included routers in a way that does not flatten.
    rest_questions = {
        path.removeprefix("/ask/")
        for path in app.openapi()["paths"]
        if path.startswith("/ask/")
    }

    mcp, _ = create_mcp_server(rag)
    mcp_questions = {
        tool.name.removeprefix("ask_")
        for tool in await mcp.list_tools()
        if tool.name.startswith("ask_")
    }

    assert rest_questions == set(ANSWER_FUNCTIONS)
    assert mcp_questions == set(ANSWER_FUNCTIONS)


@pytest.mark.asyncio
async def test_parity_is_not_asserted_by_shared_symbol_alone(rag, rest):
    """A guard on this file's own method.

    If the adapters ever stop calling the shared functions, the tests above must
    be what fails — so this one proves they would, by making the shared function
    return something recognisable and requiring it to surface on both sides.
    """
    import weave.model.answers as answers

    sentinel = {
        "question": "features",
        "nodes": [{"id": "SENTINEL", "type": "Feature", "locator": None}],
        "count": 1,
        "truncated": False,
    }

    async def fake_features(graph, *, feature=None):
        return dict(sentinel)

    original = answers.ask_features
    answers.ANSWER_FUNCTIONS["features"] = fake_features
    answers.ask_features = fake_features
    try:
        app = FastAPI()
        app.include_router(create_ask_routes(rag))
        with TestClient(app) as client:
            rest_answer = client.get("/ask/features").json()
        mcp_answer = _unwrap(await _via_mcp(rag, "features", {}))
    finally:
        answers.ANSWER_FUNCTIONS["features"] = original
        answers.ask_features = original

    assert _node_ids(rest_answer) == {"SENTINEL"}
    assert _node_ids(mcp_answer) == {"SENTINEL"}, (
        "the MCP tool did not route through weave.model.answers, so the parity "
        "assertions above would not have detected a divergence"
    )
