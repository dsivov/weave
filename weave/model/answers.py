"""The four questions a team asks its own history, each as one traversal (R20).

*What changed · why · what does it do · what did we learn.* Each is answered by
walking the graph and returning **nodes**, not a rendered text blob — because a
node can be cited, followed and resolved back to a document through its locator,
and a paragraph cannot.

**One service function per question, and the adapters are thin.** The REST
routers in `weave/server/routers/ask.py` and the MCP tools in
`weave/server/mcp.py` both call the functions here. That is A9: two answer
surfaces that disagree are worse than one, and the way to prevent disagreement is
to leave them nothing to disagree about. Nothing in this module knows about HTTP.

**Why the walk is typed rather than edge-labelled.** The ontology declares link
types, but graph edges carry a prose relation string — `record_commit` writes
"implemented by a commit", not `produced`. Link-type names are declarations, not
stored labels. So a traversal follows edges and admits neighbours by their
`entity_type`, which is stored on every node. Each question declares the types
its answer may contain, and the walk is a reachability search restricted to that
set: one traversal, bounded by the shape of the answer rather than by a hop
count.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from weave.model.locator import Locator, LocatorError

#: What `/ask/changes` may return: the delivery chain from a request to the run
#: that verified it.
CHANGE_TYPES = ("Feature", "ChangeRequest", "Task", "Commit", "PullRequest",
                "IntegrationRun")

#: What `/ask/why` may return: the decision record and the artifacts it justifies.
WHY_TYPES = ("ArchitectureDecisionRecord", "ChangeRequest", "Task", "PRD", "RFC")

#: What `/ask/features` may return: a capability and everything that describes it.
FEATURE_TYPES = ("Feature", "Module", "PRD", "RFC", "Diagram")

#: What `/ask/learnings` may return.
LEARNING_TYPES = ("Review", "Insight")

#: A ceiling on how much graph one question may walk. Not a correctness bound —
#: the type filter is — but a question asked against a large graph should return
#: a large answer slowly, not take the server with it.
MAX_NODES = 2000


#: Content an answer carries through, in the order it is assembled.
CONTENT_FIELDS = (
    "title", "status", "summary", "verdict", "statement", "sha",
    "reviewer", "confidence", "text", "asked_by", "url", "path",
)

#: The fields that can serve as a node's human-readable name, most specific
#: first. `label` is the first non-empty one, falling back to the id — which is
#: the right answer for Features and Changes, whose ids *are* their names.
#:
#: Kept beside `CONTENT_FIELDS` on purpose: they used to live in two files and
#: overlapped on one word. `tests/test_answer_labels.py` asserts that every
#: content field is either reachable from here or declared as not being a name.
LABEL_FIELDS = ("title", "statement", "summary", "text")


def _node_view(node_id: str, node: Dict[str, Any]) -> Dict[str, Any]:
    """One node, as an answer carries it.

    The locator is surfaced as a structure rather than left as four flat
    properties, because the caller's next move is almost always to resolve it.
    A node with a **partial** locator is reported with `locator: None` and a
    `locator_error` rather than raising: one malformed node must not make an
    otherwise good answer unavailable, and `scripts/check_locators.py` is what
    exists to find it.
    """
    view: Dict[str, Any] = {
        "id": node_id,
        "type": node.get("entity_type") or "",
    }
    for field in CONTENT_FIELDS:
        if node.get(field) not in (None, ""):
            view[field] = node[field]

    # The one human-readable name for this node (U3).
    #
    # **This belongs here and not in a renderer.** The UI labelled nodes from
    # `title, name, entity_name, id` while this whitelist emits content in
    # `statement`, `summary` and `text` — two lists written independently,
    # intersecting on `title` alone. So every Insight and Review rendered as a
    # raw id (`insight:T-P1-USERS:0`) with good text sitting unread in the
    # payload, and Features looked identical while being fine, because their ids
    # *are* their names.
    #
    # A longer list in the renderer would miss the next field the same way. The
    # server is the thing that knows which field carries the content — it is
    # assembling the whitelist — so it says so once, and **MCP gets it too**
    # (A9). Today an agent reading learnings receives the same bag of fields and
    # has to guess, which is the same defect without a screen to notice it on.
    view["label"] = next(
        (str(node[f]) for f in LABEL_FIELDS if node.get(f) not in (None, "")),
        node_id,
    )

    try:
        locator = Locator.from_node_properties(node)
        view["locator"] = locator.to_dict() if locator else None
    except LocatorError as e:
        view["locator"] = None
        view["locator_error"] = str(e)
    return view


async def _walk(
    graph,
    seeds: Sequence[str],
    admitted_types: Iterable[str],
    *,
    max_nodes: int = MAX_NODES,
) -> List[Dict[str, Any]]:
    """Breadth-first from *seeds*, admitting neighbours of an admitted type.

    The seeds themselves are admitted regardless of type — a question asked
    *about* a node includes that node in its answer even when the node is not
    one of the types the answer is otherwise made of.
    """
    admitted: Set[str] = set(admitted_types)
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    queue: deque[tuple[str, bool]] = deque((s, True) for s in seeds if s)

    while queue and len(out) < max_nodes:
        node_id, is_seed = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)

        node = await graph.get_node(node_id)
        if node is None:
            continue
        if not is_seed and (node.get("entity_type") or "") not in admitted:
            continue

        out.append(_node_view(node_id, node))

        edges = await graph.get_node_edges(node_id) or []
        for src, tgt in edges:
            # Undirected in practice: an audit edge may be recorded in either
            # direction, and "what changed" wants the chain, not the arrow.
            for neighbour in (src, tgt):
                if neighbour and neighbour not in seen:
                    queue.append((neighbour, False))

    return out


def _answer(question: str, nodes: List[Dict[str, Any]], **context) -> Dict[str, Any]:
    return {
        "question": question,
        "nodes": nodes,
        "count": len(nodes),
        "truncated": len(nodes) >= MAX_NODES,
        **context,
    }


async def _seeds_of_type(graph, types: Iterable[str]) -> List[str]:
    """Every node of the given types — the starting set when no anchor is named.

    Uses the storage's own label listing where it has one, which every carried
    adapter does, rather than a scan the port does not offer.
    """
    wanted = set(types)
    labels = await graph.get_all_labels()
    seeds: List[str] = []
    for label in labels or []:
        node = await graph.get_node(label)
        if node and (node.get("entity_type") or "") in wanted:
            seeds.append(label)
    return seeds


# ── the four questions ───────────────────────────────────────────────────────


async def ask_changes(graph, *, feature: Optional[str] = None) -> Dict[str, Any]:
    """*What changed* — the delivery chain (R20).

    Anchored on a feature when one is named; otherwise every change request in
    the workspace and what came of it.
    """
    seeds = [feature] if feature else await _seeds_of_type(graph, ("ChangeRequest",))
    nodes = await _walk(graph, seeds, CHANGE_TYPES)
    return _answer("changes", nodes, feature=feature or None)


async def ask_why(graph, *, node: str) -> Dict[str, Any]:
    """*Why* — the decision record behind a node, and what it justifies.

    Requires an anchor: "why" is a question about something. Without a node it
    would be "list every decision", which is a different question and one
    `/ask/features` already answers better.
    """
    if not (node or "").strip():
        raise ValueError("ask_why needs a node to ask about")
    nodes = await _walk(graph, [node], WHY_TYPES)
    decisions = [n for n in nodes if n["type"] == "ArchitectureDecisionRecord"]
    return _answer("why", nodes, node=node, decisions=len(decisions))


async def ask_features(graph, *, feature: Optional[str] = None) -> Dict[str, Any]:
    """*What does it do* — capabilities, and what describes each one."""
    seeds = [feature] if feature else await _seeds_of_type(graph, ("Feature",))
    nodes = await _walk(graph, seeds, FEATURE_TYPES)
    return _answer("features", nodes, feature=feature or None)


async def ask_learnings(graph, *, scope: Optional[str] = None) -> Dict[str, Any]:
    """*What did we learn* — reviews and the insights they yielded.

    Scoped to a node when one is named (what did we learn *here*), otherwise
    every review and insight in the workspace.
    """
    if scope:
        seeds = [scope]
    else:
        seeds = await _seeds_of_type(graph, LEARNING_TYPES)
    nodes = await _walk(graph, seeds, LEARNING_TYPES)
    if scope:
        # The anchor is admitted as a seed but is not itself a learning; the
        # answer to "what did we learn about X" should not contain X.
        nodes = [n for n in nodes if n["type"] in LEARNING_TYPES]
    return _answer("learnings", nodes, scope=scope or None)


#: The four, by the name their REST path and MCP tool share. Adapters iterate
#: this rather than hard-coding a list, so a fifth question cannot be added to
#: one surface and forgotten on the other (A9).
ANSWER_FUNCTIONS = {
    "changes": ask_changes,
    "why": ask_why,
    "features": ask_features,
    "learnings": ask_learnings,
}
