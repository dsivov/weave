"""The four questions, over HTTP.

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET /ask/changes?feature=   — what changed
  GET /ask/why?node=          — why
  GET /ask/features?feature=  — what does it do
  GET /ask/learnings?scope=   — what did we learn

**Thin on purpose.** Every route here is one call into
:mod:`weave.model.answers` and nothing else — no filtering, no reshaping, no
second opinion about which nodes belong in an answer. The MCP tools call the
same functions, and A9 says the human and agent surfaces never diverge; the way
to guarantee that is to leave the two adapters nothing to disagree about.

The routes are **generated from `ANSWER_FUNCTIONS`** rather than written out one
by one, so a fifth question cannot appear here and be forgotten in MCP. That is
the A9 failure mode expressed as a loop instead of as a rule someone remembers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from weave.model.answers import ANSWER_FUNCTIONS
from weave.server.utils import get_combined_auth_dependency

#: The query parameter each question is anchored by, and whether it is required.
#: `why` is the one that must have an anchor: without a node it degenerates into
#: "list every decision", which is a different question.
ANCHORS = {
    "changes": ("feature", False),
    "why": ("node", True),
    "features": ("feature", False),
    "learnings": ("scope", False),
}

QUESTION_SUMMARY = {
    "changes": "What changed — the delivery chain for a feature",
    "why": "Why — the decision record behind a node, and what it justifies",
    "features": "What does it do — capabilities and what describes them",
    "learnings": "What did we learn — reviews and the insights they yielded",
}


class NodeView(BaseModel):
    id: str
    type: str = ""
    locator: Optional[Dict[str, Any]] = None
    locator_error: Optional[str] = None

    model_config = {"extra": "allow"}


class AnswerResponse(BaseModel):
    question: str
    nodes: List[NodeView] = Field(default_factory=list)
    count: int = 0
    truncated: bool = False

    model_config = {"extra": "allow"}


def _require_graph(rag):
    graph = getattr(rag, "chunk_entity_relation_graph", None)
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="The answer surface requires the governance engine. Set WEAVE_ENABLE_QUADRUPLE=true (this is separate from WEAVE_ENABLE_TEAM, which may already be on).",
        )
    return graph


def create_ask_routes(rag, *, api_key: Optional[str] = None):
    """Build the /ask router bound to *rag*.

    No workspace resolver argument: the graph reached through `rag` is already
    the current workspace's, because the proxy resolves per request. A second
    workspace parameter here would be a second source of truth about which
    tenant is asking.
    """
    router = APIRouter(prefix="/ask", tags=["ask"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _make_route(question: str, param: str, required: bool):
        answer = ANSWER_FUNCTIONS[question]

        async def handler(
            value: str = Query(
                default=... if required else "",
                alias=param,
                description=f"The node this question is anchored on ({param}).",
            ),
        ):
            try:
                return await answer(_require_graph(rag), **{param: value or None})
            except ValueError as e:
                # `ask_why` refusing an empty anchor is the caller's mistake.
                raise HTTPException(status_code=400, detail=str(e))

        handler.__name__ = f"ask_{question}"
        return handler

    for question, (param, required) in ANCHORS.items():
        router.add_api_route(
            f"/{question}",
            _make_route(question, param, required),
            methods=["GET"],
            response_model=AnswerResponse,
            dependencies=[Depends(combined_auth)],
            summary=QUESTION_SUMMARY[question],
            name=f"ask_{question}",
        )

    return router
