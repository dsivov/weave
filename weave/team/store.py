"""Weave task state — the coordination store (P1).

A :class:`WeaveTask` is the unit the pull scheduler dispatches: it carries the
lifecycle ``status`` (the claim primitive operates on it), a ``priority``, and
the two edges the scheduler reasons over — ``depends_on`` (ordering) and
``touches`` (module overlap, to keep parallel claims collision-free). The store
is Weave-owned Weave state; tasks are also reflected onto the graph as audit edges
when actions fire. Lean InMemory / Json backends, mirroring the flow RunStore.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from weave_core.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")

TASK_STATUSES = ("pending", "in_progress", "review", "approved", "testing", "done", "blocked")
PRIORITY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}


@dataclass
class WeaveTask:
    id: str
    title: str = ""
    status: str = "pending"
    priority: str = "normal"
    description: str = ""
    change_request: Optional[str] = None
    touches: List[str] = field(default_factory=list)      # module names this task will edit
    depends_on: List[str] = field(default_factory=list)   # task ids that must be done first
    assignee: Optional[str] = None                        # the worker that claimed it
    created_by: Optional[str] = None
    # the artifact chain the dev loop produces (P3): Task → Commit* → PullRequest → Review*
    commits: List[Dict[str, Any]] = field(default_factory=list)
    pull_request: Optional[Dict[str, Any]] = None
    reviews: List[Dict[str, Any]] = field(default_factory=list)
    learnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "priority": self.priority, "description": self.description,
            "change_request": self.change_request, "touches": list(self.touches),
            "depends_on": list(self.depends_on), "assignee": self.assignee,
            "created_by": self.created_by,
            "commits": [dict(c) for c in self.commits],
            "pull_request": dict(self.pull_request) if self.pull_request else None,
            "reviews": [dict(r) for r in self.reviews], "learnings": list(self.learnings),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WeaveTask":
        return cls(
            id=d["id"], title=d.get("title", ""), status=d.get("status", "pending"),
            priority=d.get("priority", "normal"), description=d.get("description", ""),
            change_request=d.get("change_request"),
            touches=list(d.get("touches") or []), depends_on=list(d.get("depends_on") or []),
            assignee=d.get("assignee"), created_by=d.get("created_by"),
            commits=[dict(c) for c in (d.get("commits") or [])],
            pull_request=(dict(d["pull_request"]) if d.get("pull_request") else None),
            reviews=[dict(r) for r in (d.get("reviews") or [])],
            learnings=list(d.get("learnings") or []),
        )


class WeaveTaskStore(ABC):
    def save(self, workspace: str, task: WeaveTask) -> None:
        self._write(workspace, task.id, task.to_dict())

    def get(self, workspace: str, task_id: str) -> Optional[WeaveTask]:
        d = self._read(workspace, task_id)
        return WeaveTask.from_dict(d) if d is not None else None

    def list(self, workspace: str, *, status: Optional[str] = None) -> List[WeaveTask]:
        out = [WeaveTask.from_dict(d) for d in self._all(workspace)]
        if status is not None:
            out = [t for t in out if t.status == status]
        return out

    def delete(self, workspace: str, task_id: str) -> bool:
        return self._delete(workspace, task_id)

    @abstractmethod
    def _write(self, ws: str, tid: str, d: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _read(self, ws: str, tid: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _all(self, ws: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def _delete(self, ws: str, tid: str) -> bool: ...


class InMemoryWeaveTaskStore(WeaveTaskStore):
    def __init__(self) -> None:
        self._d: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _ws(self, ws: str) -> Dict[str, Dict[str, Any]]:
        return self._d.setdefault(ws, {})

    def _write(self, ws, tid, d): self._ws(ws)[tid] = dict(d)
    def _read(self, ws, tid): d = self._ws(ws).get(tid); return dict(d) if d else None
    def _all(self, ws): return [dict(d) for d in self._ws(ws).values()]
    def _delete(self, ws, tid): return self._ws(ws).pop(tid, None) is not None


class JsonWeaveTaskStore(WeaveTaskStore):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"weave_tasks_{name}.json")

    def _load(self, ws: str) -> Dict[str, Dict[str, Any]]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"WeaveTaskStore could not read {p}: {e}")
            return {}

    def _dump(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _write(self, ws, tid, d):
        data = self._load(ws); data[tid] = d; self._dump(ws, data)

    def _read(self, ws, tid):
        return self._load(ws).get(tid)

    def _all(self, ws):
        return list(self._load(ws).values())

    def _delete(self, ws, tid):
        data = self._load(ws)
        if tid in data:
            del data[tid]; self._dump(ws, data); return True
        return False
