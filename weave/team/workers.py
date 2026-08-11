"""Weave worker registry — fleet presence + control-state (P3).

A **worker** is a registered dev principal: a headless Claude Code container loop
or an interactive developer. On start it calls :meth:`WorkerRegistry.register`
(authenticated identity + host + capabilities + goal) and then **heartbeats**, so
it appears in the platform's live fleet. Each heartbeat returns the worker's
**control-state** — ``run`` / ``pause`` / ``stop`` — which the loop checks between
steps, giving supervisors a clean pause / resume / stop. A worker whose heartbeat
goes stale is reported ``offline`` without touching its stored control-state.

Coordination stays *pull* and Weave-native: the registry is state a supervisor reads
and writes; the platform never dials the worker. It mirrors the task store — lean
InMemory / Json backends — and, when a graph is wired, reflects each worker as a
``Worker`` node so the fleet is queryable alongside the rest of the project.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from weave_core.utils import logger

from weave_core.store.record import (
    InMemoryRecordStore, JsonRecordStore, RecordStore,
)
from weave_core.graph.types import RelationContext

# control-state the loop obeys; `run` is the steady state, `stop` is terminal.
WORKER_CONTROLS = ("run", "pause", "stop")
# a worker silent longer than this (seconds) is reported offline in listings.
HEARTBEAT_TTL = 90.0


class WorkerOwnershipError(Exception):
    """A principal tried to register/heartbeat a worker id owned by another (403)."""


@dataclass
class WeaveWorker:
    id: str
    role: str = "developer"
    host: str = ""
    capabilities: List[str] = field(default_factory=list)
    goal: str = ""
    owner: str = ""                         # the principal that first registered this id
    control: str = "run"                    # run · pause · stop  (what the loop obeys)
    status: str = "active"                  # active · paused · stopped  (stored)
    current_task: Optional[str] = None
    registered_at: float = 0.0
    last_heartbeat: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WeaveWorker":
        return cls(
            id=d["id"], role=d.get("role", "developer"), host=d.get("host", ""),
            capabilities=list(d.get("capabilities") or []), goal=d.get("goal", ""),
            owner=d.get("owner", ""),
            control=d.get("control", "run"), status=d.get("status", "active"),
            current_task=d.get("current_task"),
            registered_at=d.get("registered_at", 0.0),
            last_heartbeat=d.get("last_heartbeat", 0.0))


class WeaveWorkerStore(RecordStore[WeaveWorker]):
    """CRUD over workers. The storage shape is shared with the dev-host registry
    (see :mod:`weave_core.store.record`); only the record type differs."""

    record_type = WeaveWorker


class InMemoryWeaveWorkerStore(InMemoryRecordStore[WeaveWorker], WeaveWorkerStore):
    pass


class JsonWeaveWorkerStore(JsonRecordStore[WeaveWorker], WeaveWorkerStore):
    filename_prefix = "weave_workers"


class WorkerRegistry:
    """The live fleet. Register + heartbeat give presence; a supervisor's
    pause/resume/stop rides back to the worker on its next heartbeat."""

    def __init__(
        self,
        worker_store: WeaveWorkerStore,
        *,
        rag_resolver: Optional[Callable[[str], Any]] = None,
        now: Callable[[], float] = time.time,
        heartbeat_ttl: float = HEARTBEAT_TTL,
    ) -> None:
        self._workers = worker_store
        self._resolve_rag = rag_resolver
        self._now = now
        self._ttl = heartbeat_ttl

    @property
    def store(self) -> WeaveWorkerStore:
        return self._workers

    async def register(
        self, workspace: str, worker_id: str, *, role: str = "developer",
        host: str = "", capabilities: Optional[List[str]] = None, goal: str = "",
        owner: str = "",
    ) -> WeaveWorker:
        """Register (or re-register) a worker into the fleet. Idempotent on id — a
        restart re-registers cleanly. Two invariants protect the fleet:

        * **Ownership** — the first registrant *owns* the id; a different
          principal re-registering it is refused (:class:`WorkerOwnershipError`),
          so a developer can't hijack a peer's worker.
        * **Terminal stop** — if a supervisor stopped the worker, re-registration
          keeps it stopped. A restarting container can't undo its own kill; a
          supervisor must ``resume`` it first."""
        now = self._now()
        existing = self._workers.get(workspace, worker_id)
        if existing is not None and existing.owner and owner and existing.owner != owner:
            raise WorkerOwnershipError(
                f"worker '{worker_id}' is owned by another principal")
        terminal = existing is not None and existing.control == "stop"
        w = WeaveWorker(
            id=worker_id, role=role, host=host,
            capabilities=list(capabilities or []), goal=goal,
            owner=owner or (existing.owner if existing else ""),
            control="stop" if terminal else "run",
            status="stopped" if terminal else "active", current_task=None,
            registered_at=existing.registered_at if existing else now,
            last_heartbeat=now)
        self._workers.save(workspace, w)
        await self._reflect(workspace, w, "registers a worker",
                            f"{role} worker {worker_id} joined the fleet")
        logger.info(f"Weave: worker '{worker_id}' ({role}) registered in '{workspace}'"
                    + (" [still stopped]" if terminal else ""))
        return w

    def heartbeat(self, workspace: str, worker_id: str, *,
                  current_task: Optional[str] = None, owner: Optional[str] = None,
                  ) -> Dict[str, Any]:
        """Refresh presence and return the control-state the loop must obey. Only
        the worker's owner may heartbeat it (so a stray caller can't keep a dead
        worker looking alive); a heartbeat never revives a stopped worker."""
        w = self._workers.get(workspace, worker_id)
        if w is None:
            raise KeyError(worker_id)
        if owner is not None and w.owner and w.owner != owner:
            raise WorkerOwnershipError(
                f"worker '{worker_id}' is owned by another principal")
        w.last_heartbeat = self._now()
        if current_task is not None:
            w.current_task = current_task or None
        self._workers.save(workspace, w)
        return {"worker": worker_id, "control": w.control, "status": w.status,
                "current_task": w.current_task}

    def set_control(self, workspace: str, worker_id: str, action: str) -> WeaveWorker:
        """Supervisor control. ``pause``/``resume`` toggle the run state; ``stop``
        is terminal (a stopped worker won't resume)."""
        w = self._workers.get(workspace, worker_id)
        if w is None:
            raise KeyError(worker_id)
        if w.control == "stop":
            raise ValueError(f"worker '{worker_id}' is stopped (terminal)")
        if action == "pause":
            w.control, w.status = "pause", "paused"
        elif action == "resume":
            w.control, w.status = "run", "active"
        elif action == "stop":
            w.control, w.status = "stop", "stopped"
        else:
            raise ValueError(f"unknown control action '{action}'")
        self._workers.save(workspace, w)
        logger.info(f"Weave: worker '{worker_id}' → {action} in '{workspace}'")
        return w

    def set_goal(self, workspace: str, worker_id: str, goal: str) -> WeaveWorker:
        """Redirect a worker: change what it is *for*, without stopping it.

        Not a control state — the loop keeps running and picks the new goal up on
        its next heartbeat, between steps, exactly as it picks up a pause. That
        is what makes a redirect safe mid-run: the worker finishes the step it is
        on and reads its new instructions before starting the next.

        A stopped worker is not redirected. `stop` is terminal, and quietly
        giving a stopped worker a new goal would leave a record implying it went
        and did something.
        """
        w = self._workers.get(workspace, worker_id)
        if w is None:
            raise KeyError(worker_id)
        if w.control == "stop":
            raise ValueError(f"worker '{worker_id}' is stopped (terminal)")
        w.goal = goal
        self._workers.save(workspace, w)
        logger.info(f"Weave: worker '{worker_id}' redirected in '{workspace}'")
        return w

    def get(self, workspace: str, worker_id: str) -> Optional[Dict[str, Any]]:
        w = self._workers.get(workspace, worker_id)
        return self._view(w) if w is not None else None

    def list(self, workspace: str, *, include_offline: bool = True) -> List[Dict[str, Any]]:
        """The fleet. Each worker's effective status folds in liveness: a stale
        heartbeat reads ``offline`` (the stored control-state is left intact)."""
        out = [self._view(w) for w in self._workers.list(workspace)]
        if not include_offline:
            out = [v for v in out if v["status"] != "offline"]
        return sorted(out, key=lambda v: v["id"])

    # -- internals -----------------------------------------------------------

    def _view(self, w: WeaveWorker) -> Dict[str, Any]:
        d = w.to_dict()
        # effective liveness: stale + not stopped → offline (non-destructive)
        stale = (self._now() - w.last_heartbeat) > self._ttl
        if stale and w.status != "stopped":
            d["status"] = "offline"
        d["stale"] = stale
        return d

    async def _reflect(self, workspace: str, w: WeaveWorker, relation: str, why: str) -> None:
        """Reflect the worker onto the graph as a ``Worker`` node + an audit edge
        (best-effort — presence in the store is the source of truth)."""
        if self._resolve_rag is None:
            return
        rag = self._resolve_rag(workspace)
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is not None:
            try:
                await graph.upsert_node(w.id, {
                    "entity_id": w.id, "entity_type": "Worker", "role": w.role,
                    "host": w.host, "status": w.status, "control": w.control})
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave worker node upsert skipped: {e}")
        if hasattr(rag, "emit_decision_trace"):
            rc = RelationContext(decision_trace=why, approved_by=w.role,
                                 approved_via="system",
                                 provenance=f"weave:worker:{w.id}", confidence_score=1.0)
            try:
                await rag.emit_decision_trace(w.role, w.id, relation, rc)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave worker audit skipped: {e}")
