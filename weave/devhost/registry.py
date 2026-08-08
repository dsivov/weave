"""Dev-host registry — machines that carry autonomous developers (P8).

A **dev host** is a machine that runs developer agents. The daemon on it
(:mod:`weave.devhost.daemon`) registers the machine here, then
heartbeats; each heartbeat reply tells it what the team wants the machine to be
doing. Everything the fleet needs flows in that one direction, which is what
lets a host sit behind NAT on someone's desk or in a private VPC with no inbound
access at all.

**Why the reply carries a desired worker count.** Scaling a remote machine is
the one supervisory act that would otherwise need Weave to dial out. Instead the
board writes ``desired_workers`` onto the host record, and the host learns it on
its next heartbeat and reconciles — starting or stopping containers itself. So
"run three developers in Berlin" is a piece of state the machine reads, not a
command sent to it, and D2 (Weave never dials a worker) survives contact with
remote fleets.

**Draining.** Workers have ``run``/``pause``/``stop``; a machine also needs
``drain``. Stopping a host outright abandons whatever its containers have
already claimed, so ``drain`` means *claim nothing new, finish what you hold*.
It is how a machine leaves rotation without stranding tasks mid-flight.

**The seat.** Every container on a host shares that machine's one Claude
subscription seat, provisioned by an interactive login on the box (decision D9 —
subscription only, never an API key). A machine whose seat is missing or expired
cannot do any work at all, so the host reports seat health on every heartbeat
and the board can say *why* a machine is idle instead of just showing zero
progress.
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

# What a host may be told to do. `drain` has no worker-level equivalent: it is
# the state where a machine finishes its in-flight work and claims nothing new.
HOST_CONTROLS = ("run", "drain", "pause", "stop")
# A host silent longer than this is reported offline. Longer than the worker TTL
# because a host heartbeats on a slower cadence — it has nothing to poll for.
HOST_HEARTBEAT_TTL = 180.0

# Seat health, as reported by the host's own preflight.
SEAT_STATES = ("ok", "missing", "expired", "unknown")


class HostOwnershipError(Exception):
    """A principal tried to register/heartbeat a host id owned by another (403)."""


@dataclass
class DevHost:
    """One machine carrying developer agents.

    The split that matters: ``desired_workers`` is what the *team* wants and is
    written by a supervisor; ``workers`` is what the machine actually reports
    running. The daemon's job is to close the gap between them.
    """

    id: str
    machine: str = ""                       # hostname, for humans reading the board
    owner: str = ""                         # principal that first registered this id
    capabilities: List[str] = field(default_factory=list)
    repo: str = ""                          # the clone worktrees are cut from
    base_branch: str = "main"               # what each task branch starts from
    image: str = ""                         # container image the workers run

    desired_workers: int = 0                # supervisor-set; the host reconciles to it
    workers: List[str] = field(default_factory=list)   # worker ids actually running
    seat: str = "unknown"                   # ok · missing · expired · unknown
    seat_detail: str = ""                   # e.g. "max via claude.ai (dev@…)"

    control: str = "run"                    # run · drain · pause · stop
    status: str = "active"                  # active · draining · paused · stopped
    registered_at: float = 0.0
    last_heartbeat: float = 0.0
    version: str = ""                       # daemon version, for rollout visibility

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DevHost":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


class DevHostStore(RecordStore[DevHost]):
    record_type = DevHost


class InMemoryDevHostStore(InMemoryRecordStore[DevHost], DevHostStore):
    pass


class JsonDevHostStore(JsonRecordStore[DevHost], DevHostStore):
    filename_prefix = "weave_hosts"


class DevHostRegistry:
    """The machines. Register + heartbeat give presence; the heartbeat reply is
    the only channel by which a supervisor's intent reaches a remote box."""

    def __init__(
        self,
        host_store: DevHostStore,
        *,
        rag_resolver: Optional[Callable[[str], Any]] = None,
        now: Callable[[], float] = time.time,
        heartbeat_ttl: float = HOST_HEARTBEAT_TTL,
        project_service: Optional[Any] = None,
        worker_registry: Optional[Any] = None,
    ) -> None:
        self._hosts = host_store
        self._resolve_rag = rag_resolver
        self._now = now
        self._ttl = heartbeat_ttl
        self._projects = project_service
        self._workers = worker_registry

    @property
    def store(self) -> DevHostStore:
        return self._hosts

    async def register(
        self, workspace: str, host_id: str, *, machine: str = "",
        capabilities: Optional[List[str]] = None, repo: str = "",
        base_branch: str = "main", image: str = "", version: str = "",
        seat: str = "unknown", seat_detail: str = "", owner: str = "",
    ) -> DevHost:
        """Register (or re-register) a machine. Idempotent on id, so a daemon
        restart or a reboot re-registers cleanly.

        Three things survive re-registration, because a machine must not be able
        to talk its way out of a supervisor's decision by restarting:

        * **Ownership** — the first registrant owns the id.
        * **A terminal stop** — a stopped host stays stopped until resumed.
        * **The desired worker count** — the team's intent outlives the process.
        """
        now = self._now()
        existing = self._hosts.get(workspace, host_id)
        if existing is not None and existing.owner and owner and existing.owner != owner:
            raise HostOwnershipError(f"dev host '{host_id}' is owned by another principal")
        if seat not in SEAT_STATES:
            seat = "unknown"
        terminal = existing is not None and existing.control == "stop"
        h = DevHost(
            id=host_id, machine=machine, owner=owner or (existing.owner if existing else ""),
            capabilities=list(capabilities or []), repo=repo, base_branch=base_branch,
            image=image, version=version, seat=seat, seat_detail=seat_detail,
            desired_workers=existing.desired_workers if existing else 0,
            workers=[],                      # nothing is running yet; the host will report
            control="stop" if terminal else "run",
            status="stopped" if terminal else "active",
            registered_at=existing.registered_at if existing else now,
            last_heartbeat=now)
        self._hosts.save(workspace, h)
        await self._reflect(workspace, h, f"dev host {host_id} ({machine}) joined the fleet")
        logger.info(f"Weave: dev host '{host_id}' registered in '{workspace}'"
                    f" [seat={seat}]" + (" [still stopped]" if terminal else ""))
        return h

    def heartbeat(self, workspace: str, host_id: str, *,
                  workers: Optional[List[str]] = None, seat: Optional[str] = None,
                  seat_detail: Optional[str] = None, owner: Optional[str] = None,
                  ) -> Dict[str, Any]:
        """Refresh presence and return what the machine should be doing.

        The reply is the whole remote-control channel: the control-state to obey
        and the worker count to reconcile to. A heartbeat never revives a stopped
        host, and only the owner may send one — otherwise a stray caller could
        keep a dead machine looking alive on the board.
        """
        h = self._hosts.get(workspace, host_id)
        if h is None:
            raise KeyError(host_id)
        if owner is not None and h.owner and h.owner != owner:
            raise HostOwnershipError(f"dev host '{host_id}' is owned by another principal")
        h.last_heartbeat = self._now()
        if workers is not None:
            h.workers = list(workers)
        if seat is not None and seat in SEAT_STATES:
            h.seat = seat
        if seat_detail is not None:
            h.seat_detail = seat_detail
        self._hosts.save(workspace, h)
        # A drained/paused/stopped host is told to hold nothing new, so the
        # reconcile target is zero regardless of what the team asked for while
        # it was running.
        target = h.desired_workers if h.control == "run" else 0
        reply = {"host": host_id, "control": h.control, "status": h.status,
                 "desired_workers": target, "base_branch": h.base_branch,
                 "repo": h.repo, "image": h.image}
        # Onboarding rides on the heartbeat: the workspace's project definition
        # is what a machine works on, and it wins over whatever this box was
        # started with. That is how a new host needs no local project config,
        # and how changing the base branch reaches every machine at once.
        if self._projects is not None:
            project = self._projects.get(workspace).onboarding()
            reply.update({k: v for k, v in project.items() if v})
            reply["project"] = project
        # Two control planes have to compose here. The host count says *how many*
        # developers should exist on this machine; a per-worker pause or stop
        # says *not that one*. Without telling the host which workers are held,
        # it would see a supervisor-stopped container missing and dutifully
        # restart it — the machine would silently undo a human's decision.
        reply["held_workers"] = self._held_workers(workspace, h)
        return reply

    def _held_workers(self, workspace: str, h: DevHost) -> List[str]:
        """Workers on this machine a supervisor has paused or stopped."""
        if self._workers is None:
            return []
        try:
            fleet = self._workers.list(workspace)
        except Exception:  # pragma: no cover - defensive
            return []
        known = set(h.workers)
        return sorted(
            w["id"] for w in fleet
            if w.get("control") in ("pause", "stop")
            and (w["id"] in known or w.get("host") == h.id or w["id"].startswith(f"{h.id}-")))

    def set_control(self, workspace: str, host_id: str, action: str) -> DevHost:
        """Supervisor control. ``stop`` is terminal; ``drain`` lets in-flight work
        finish; ``resume`` returns a drained or paused host to service."""
        h = self._hosts.get(workspace, host_id)
        if h is None:
            raise KeyError(host_id)
        if h.control == "stop":
            raise ValueError(f"dev host '{host_id}' is stopped (terminal)")
        if action == "pause":
            h.control, h.status = "pause", "paused"
        elif action == "drain":
            h.control, h.status = "drain", "draining"
        elif action == "resume":
            h.control, h.status = "run", "active"
        elif action == "stop":
            h.control, h.status = "stop", "stopped"
        else:
            raise ValueError(f"unknown control action '{action}'")
        self._hosts.save(workspace, h)
        logger.info(f"Weave: dev host '{host_id}' → {action} in '{workspace}'")
        return h

    def scale(self, workspace: str, host_id: str, desired: int) -> DevHost:
        """Set how many developers the team wants this machine to run.

        This does not start anything. It records intent; the host reads it on its
        next heartbeat and reconciles. That indirection is the point — it is how
        a machine we cannot dial still gets scaled from the board.
        """
        h = self._hosts.get(workspace, host_id)
        if h is None:
            raise KeyError(host_id)
        if desired < 0:
            raise ValueError("desired worker count cannot be negative")
        h.desired_workers = desired
        self._hosts.save(workspace, h)
        logger.info(f"Weave: dev host '{host_id}' desired_workers → {desired}")
        return h

    def get(self, workspace: str, host_id: str) -> Optional[Dict[str, Any]]:
        h = self._hosts.get(workspace, host_id)
        return self._view(h) if h is not None else None

    def list(self, workspace: str, *, include_offline: bool = True) -> List[Dict[str, Any]]:
        out = [self._view(h) for h in self._hosts.list(workspace)]
        if not include_offline:
            out = [v for v in out if v["status"] != "offline"]
        return sorted(out, key=lambda v: v["id"])

    # -- internals -----------------------------------------------------------

    def _view(self, h: DevHost) -> Dict[str, Any]:
        d = h.to_dict()
        stale = (self._now() - h.last_heartbeat) > self._ttl
        if stale and h.status != "stopped":
            d["status"] = "offline"
        d["stale"] = stale
        return d

    async def _reflect(self, workspace: str, h: DevHost, why: str) -> None:
        """Reflect the host onto the graph as a ``DevHost`` node (best-effort —
        the store stays the source of truth for presence)."""
        if self._resolve_rag is None:
            return
        rag = self._resolve_rag(workspace)
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is not None:
            try:
                await graph.upsert_node(h.id, {
                    "entity_id": h.id, "entity_type": "DevHost", "machine": h.machine,
                    "status": h.status, "control": h.control, "seat": h.seat})
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave dev-host node upsert skipped: {e}")
        if hasattr(rag, "emit_decision_trace"):
            rc = RelationContext(decision_trace=why, approved_by="devhost",
                                 approved_via="system",
                                 provenance=f"weave:host:{h.id}", confidence_score=1.0)
            try:
                await rag.emit_decision_trace(h.id, workspace, "registers a dev host", rc)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave dev-host decision trace skipped: {e}")
