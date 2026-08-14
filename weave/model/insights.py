"""The `Review` and `Insight` nodes — built **once**, for both callers (D-043).

`/ask/learnings` seeds on `entity_type in (Review, Insight)`. Until P10.1 the only
code that ever produced those types was the migration: `record_review` wrote a
`PullRequest` node and an audit edge, `record_learning` wrote a decision trace,
and neither created the node the answer reads. So on a **clean** workspace — the
state every reader of the guide is in — recording 7 learnings and 6 reviews left
*what did we learn* answering **0**, and `weave migrate reviews` was the
undocumented step that made it work (W23).

**Why this module exists rather than a second builder in the coordinator.** The
live path and the migration must produce the same node for the same entry, or a
workspace answers differently depending on which one wrote it — and the
difference would only ever show up long after both had been reviewed separately.
One builder, two callers, and the migration's second run over live-recorded data
creates nothing, which is the observable form of "they cannot differ".

**Ids are positional, and that is what makes the two agree.** The id is the task
id plus the entry's index in the task's list, and both lists are append-only, so
the index the live path computes at recording time is the index the migration
would compute later. A content hash would be stable too, but it would collapse
the same lesson learned twice on one task into one node — and R25 counts the move
by count as well as by content, because twice is a fact about the team.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

#: Node-id prefixes. Namespaced so a recorded or migrated node cannot collide
#: with an authored one.
REVIEW_PREFIX = "review"
INSIGHT_PREFIX = "insight"


def review_node_id(task_id: str, index: int) -> str:
    return f"{REVIEW_PREFIX}:{task_id}:{index}"


def insight_node_id(task_id: str, index: int) -> str:
    return f"{INSIGHT_PREFIX}:{task_id}:{index}"


def project_insight_node_id(statement: str) -> str:
    """A learning recorded against no task — `POST /weave/learnings` with no
    `task` — which has **no list to take a position in**.

    Hashed rather than positional, and it is the one place in this module where
    that is the right answer: there is no append-only list behind a project-level
    learning, so there is no index, and an id derived from a counter would depend
    on what else had been recorded first. The cost is that the same project-level
    statement recorded twice is one node — accepted, because the alternative is
    a non-deterministic id, and unlike the task path there is no source list to
    reconcile against later.
    """
    digest = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:12]
    return f"{INSIGHT_PREFIX}:project:{digest}"


def review_node(
    task_id: str, index: int, entry: Dict[str, Any], *,
    migrated_from: Optional[str] = None,
) -> Dict[str, Any]:
    """One `Review` node from a `{verdict, by, notes}` entry.

    `notes` becomes `summary`: it is an abstract written *about* the change, not
    a copy of a document, so A5 is not in play. There is no locator, because the
    entry never had one — neither path may invent provenance it does not have,
    and `scripts/check_locators.py` counts a missing locator honestly.

    `migrated_from` is set only by the migration. Its **absence** is what says a
    node was recorded live, which is true and needs no second field to say it.
    """
    node = {
        "entity_id": review_node_id(task_id, index),
        "entity_type": "Review",
        "verdict": str(entry.get("verdict") or ""),
        "reviewer": str(entry.get("by") or ""),
        "summary": str(entry.get("notes") or ""),
    }
    if migrated_from is not None:
        node["migrated_from"] = migrated_from
    return node


def insight_node(
    task_id: str, index: int, statement: str, *,
    migrated_from: Optional[str] = None,
) -> Dict[str, Any]:
    node = {
        "entity_id": insight_node_id(task_id, index),
        "entity_type": "Insight",
        "statement": str(statement),
    }
    if migrated_from is not None:
        node["migrated_from"] = migrated_from
    return node


def project_insight_node(statement: str) -> Dict[str, Any]:
    """An `Insight` recorded against the project rather than a task."""
    return {
        "entity_id": project_insight_node_id(statement),
        "entity_type": "Insight",
        "statement": str(statement),
    }


#: What the migration writes into `migrated_from`, so the live path and the
#: migration derive the string the same way rather than each formatting it.
def migrated_from_review(task_id: str, index: int) -> str:
    return f"task:{task_id}.reviews[{index}]"


def migrated_from_learning(task_id: str, index: int) -> str:
    return f"task:{task_id}.learnings[{index}]"
