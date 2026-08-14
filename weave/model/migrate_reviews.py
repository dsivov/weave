"""Lift task `reviews` and `learnings` into `Review` and `Insight` nodes (R25).

**As of D-043 this is a one-off for instances that predate P10.1.** Recording a
review or a learning now creates the typed node itself, using the same builder
in `weave.model.insights` that this module calls — so on a workspace created
after P10.1 there is nothing here to move, and a run over one reports
`nodes_created: 0`. That zero is the assertion, not a formality: it is only true
because both paths derive the same id from the same position.


Before P2, a review was an entry in a list on a task record and a learning was a
string in another list. Neither could be traversed, cited, or resolved back to a
document — which is why "what did we learn" had no answer that was not a text
dump. R19 made them object types; this moves the data that already exists.

**Idempotent by construction, not by a flag.** Node ids are derived from the task
id and the entry's position in its list, which is stable because both lists are
append-only (`record_review` and `record_learning` only append). So a second run
upserts the same ids with the same content and creates nothing. That is stronger
than a "migrated" marker, which can be lost, copied to a new environment, or set
while the write it describes half-failed.

**Position, not a content hash.** A hash would collapse two genuinely identical
learnings on one task into a single node, and R25 asserts the migration moves
100% *by count* as well as by content. Two identical insights recorded twice are
two facts about how often the team learned that thing.

**The source fields are not removed here.** R25 removes them only after M2 is
signed off, and doing both in one step would leave no way to verify the move
against the original. This module writes nodes; a later, separate change deletes
the fields.
"""

from __future__ import annotations

from typing import Any, Dict, List

from weave.model.insights import (  # noqa: F401 — re-exported: the ids are this
    INSIGHT_PREFIX,                 # module's published surface (CLI, tests).
    REVIEW_PREFIX,
    insight_node,
    insight_node_id,
    migrated_from_learning,
    migrated_from_review,
    review_node,
    review_node_id,
)
from weave_core.utils import logger


def _review_node(task_id: str, index: int, entry: Dict[str, Any]) -> Dict[str, Any]:
    """The shared builder, told this node came from a migration (D-043).

    **The construction itself lives in `weave.model.insights` and is shared with
    `record_review`.** Until P10.1 this module was the only code that produced a
    `Review` node, which is why a clean workspace had none: recording wrote an
    audit edge and the answer surface reads types. Two builders would let the
    live node and the migrated node drift apart, and the drift would surface as
    "the answer is right on the old tenant and wrong on the new one".
    """
    return review_node(task_id, index, entry,
                       migrated_from=migrated_from_review(task_id, index))


def _insight_node(task_id: str, index: int, statement: str) -> Dict[str, Any]:
    return insight_node(task_id, index, statement,
                        migrated_from=migrated_from_learning(task_id, index))


async def migrate_workspace(
    workspace: str,
    task_store,
    graph,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Move every task's `reviews` and `learnings` into nodes.

    Returns a report with the counts R25 asserts against: how many entries were
    found, how many nodes exist afterwards, and how many this run actually
    created (zero on a second run, which is what idempotent means here).
    """
    tasks = task_store.list(workspace)

    found_reviews = 0
    found_learnings = 0
    created: List[str] = []
    existing: List[str] = []

    for task in tasks:
        for index, entry in enumerate(task.reviews or []):
            found_reviews += 1
            node = _review_node(task.id, index, entry)
            await _upsert(graph, node, created, existing, dry_run)
            await _link(graph, task.id, node["entity_id"], "reviewed in", dry_run)

        for index, statement in enumerate(task.learnings or []):
            found_learnings += 1
            node = _insight_node(task.id, index, str(statement))
            await _upsert(graph, node, created, existing, dry_run)
            # Attached to the task rather than to a review: `record_learning`
            # writes a learning against a task, not against a review, so
            # inventing a review to hang it from would fabricate a relationship.
            await _link(graph, task.id, node["entity_id"], "yielded", dry_run)

    report = {
        "workspace": workspace,
        "tasks": len(tasks),
        "reviews_found": found_reviews,
        "learnings_found": found_learnings,
        "nodes_expected": found_reviews + found_learnings,
        "nodes_created": len(created),
        "nodes_already_present": len(existing),
        "dry_run": dry_run,
    }
    logger.info(f"review/learning migration: {report}")
    return report


async def _upsert(graph, node: Dict[str, Any], created, existing, dry_run) -> None:
    node_id = node["entity_id"]
    already = await graph.has_node(node_id)
    (existing if already else created).append(node_id)
    if not dry_run:
        await graph.upsert_node(node_id, node)


async def _link(graph, src: str, tgt: str, relation: str, dry_run: bool) -> None:
    """Attach the new node to the task it came from.

    Skipped when the task has no node in the graph: the task *store* is the
    source of truth for tasks, and the graph holds only those that were
    reflected onto it. A dangling edge would be worse than a node reachable only
    by type.
    """
    if dry_run or not await graph.has_node(src):
        return
    await graph.upsert_edge(src, tgt, {"relation": relation, "source": "migration"})


async def verify_workspace(workspace: str, task_store, graph) -> Dict[str, Any]:
    """Check the move landed — by count *and* by content (R25).

    Separate from `migrate_workspace` on purpose: a migration that reports its
    own success is only as trustworthy as the code path that just ran. This
    re-reads both sides and compares.
    """
    missing: List[str] = []
    mismatched: List[str] = []
    checked = 0

    for task in task_store.list(workspace):
        for index, entry in enumerate(task.reviews or []):
            checked += 1
            node = await graph.get_node(review_node_id(task.id, index))
            if node is None:
                missing.append(review_node_id(task.id, index))
            elif (
                node.get("verdict") != str(entry.get("verdict") or "")
                or node.get("summary") != str(entry.get("notes") or "")
                or node.get("reviewer") != str(entry.get("by") or "")
            ):
                mismatched.append(review_node_id(task.id, index))

        for index, statement in enumerate(task.learnings or []):
            checked += 1
            node = await graph.get_node(insight_node_id(task.id, index))
            if node is None:
                missing.append(insight_node_id(task.id, index))
            elif node.get("statement") != str(statement):
                mismatched.append(insight_node_id(task.id, index))

    return {
        "workspace": workspace,
        "checked": checked,
        "missing": missing,
        "mismatched": mismatched,
        "complete": not missing and not mismatched,
    }
