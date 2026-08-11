"""The senior-developer seat — supervision as recorded intent, never as a call out.

A senior developer dispatches work, pauses a worker mid-run, redirects one onto
something more urgent, and drains a machine for maintenance. Every one of those
reads like *the server telling a worker what to do*, and **none of them is**
(A15). The hub never dials anyone. What this module does is write state — a
worker's control field, a host's `desired_workers` — that the fleet reads back on
its **next heartbeat** and reconciles to.

That indirection is not an implementation detail to be optimised away later. It
is the whole reason a dev host can sit behind NAT on someone's desk, or in a
private VPC with no inbound access at all, and still be run from the board. A
"just POST to the host" shortcut would work on a laptop and break every real
deployment, silently, for whoever tried one first.

**No model sits in this path** (A12). Which worker gets which task is
deterministic graph logic — readiness, dependencies, and the `touches` collision
rule the coordinator already owns. The seat *operates* the lifecycle; it does not
reason about it.

**Every act carries an authenticated principal** (A6), passed in by the router
from the request's identity. Nothing here accepts an actor from a payload,
because a supervisor who could name themselves is not a supervisor — supervision
is exactly the surface where "who ordered this" has to be answerable.

**The claim protocol is untouched.** Dispatch *offers* work by ordering a queue;
it never claims on a worker's behalf. Claiming stays where it is, with its lock
and its `touches` rule, because a fleet race there is invisible until it corrupts
work — and M5's gate is that the copied claim tests pass unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from weave_core.utils import logger

#: What a supervisor may do to a single worker. `redirect` is not a control
#: state — it changes what a worker is *for*, which the loop reads as a new goal.
WORKER_ACTIONS = ("pause", "resume", "stop", "redirect")

#: What a supervisor may do to a machine. `drain` has no worker equivalent: it
#: means *finish what you hold, claim nothing new*.
HOST_ACTIONS = ("run", "drain", "pause", "stop")


class SupervisorError(ValueError):
    """A supervisory act that cannot be carried out as asked."""


class NotAuthenticated(SupervisorError):
    """A supervisory act arrived without an identity to attribute it to (A6)."""


@dataclass
class SupervisoryAct:
    """One recorded intent, and what will pick it up.

    `reaches_fleet_via` is carried deliberately: it names the *only* way this act
    travels, and every value is something the fleet pulls. If a future act needs
    a value that is not a pull, that is A15 going false and the field is where it
    shows.
    """

    act: str
    target: str
    by: str
    detail: Dict[str, Any]
    reaches_fleet_via: str = "heartbeat"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "act": self.act,
            "target": self.target,
            "by": self.by,
            "detail": dict(self.detail),
            "reaches_fleet_via": self.reaches_fleet_via,
        }


class Supervisor:
    """The seat. Composes the fleet registries; opens no connections of its own.

    Deliberately holds no transport. Everything it does is a store write, which
    is what makes "the hub never dials out" a property of the type rather than a
    rule someone has to remember while editing it.
    """

    def __init__(self, workers, hosts, coordinator=None) -> None:
        self._workers = workers
        self._hosts = hosts
        self._coordinator = coordinator

    # -- who is asking ------------------------------------------------------

    @staticmethod
    def _require_principal(by: str) -> str:
        """A6: the actor is the authenticated identity the router resolved.

        Refused rather than defaulted. A supervisory act attributed to "system"
        is one nobody can be asked about, and these are the acts most worth
        asking about.
        """
        actor = (by or "").strip()
        if not actor:
            raise NotAuthenticated(
                "a supervisory act needs an authenticated principal — "
                "who paused this worker is the first question anyone asks"
            )
        return actor

    # -- per-worker supervision --------------------------------------------

    async def control_worker(
        self, workspace: str, worker_id: str, action: str, *,
        by: str, goal: str = "",
    ) -> SupervisoryAct:
        """Pause, resume, stop or redirect one worker.

        Nothing is sent anywhere. The worker's loop reads its control state on
        its **next heartbeat**, between steps — which is what makes a pause land
        on a clean worktree instead of halfway through an edit.
        """
        actor = self._require_principal(by)
        if action not in WORKER_ACTIONS:
            raise SupervisorError(
                f"'{action}' is not a supervisory action; expected one of "
                f"{', '.join(WORKER_ACTIONS)}"
            )

        if action == "redirect":
            if not goal.strip():
                raise SupervisorError("a redirect needs a goal to redirect to")
            worker = self._workers.set_goal(workspace, worker_id, goal.strip())
            detail = {"goal": worker.goal, "status": worker.status,
                      "control": worker.control}
        else:
            worker = self._workers.set_control(workspace, worker_id, action)
            detail = {"control": worker.control, "status": worker.status}

        logger.info(
            f"supervisor: {actor} → {action} worker '{worker_id}' "
            f"(read back on its next heartbeat)"
        )
        return SupervisoryAct(act=f"worker.{action}", target=worker_id,
                              by=actor, detail=detail)

    # -- per-machine supervision -------------------------------------------

    async def control_host(
        self, workspace: str, host_id: str, action: str, *, by: str,
    ) -> SupervisoryAct:
        """Run, drain, pause or stop a machine. Recorded; never dialled."""
        actor = self._require_principal(by)
        if action not in HOST_ACTIONS:
            raise SupervisorError(
                f"'{action}' is not a host control; expected one of "
                f"{', '.join(HOST_ACTIONS)}"
            )
        host = self._hosts.set_control(workspace, host_id, action)
        logger.info(f"supervisor: {actor} → {action} host '{host_id}'")
        return SupervisoryAct(
            act=f"host.{action}", target=host_id, by=actor,
            detail={"control": host.control, "status": host.status},
        )

    async def scale_host(
        self, workspace: str, host_id: str, desired: int, *, by: str,
    ) -> SupervisoryAct:
        """Ask a machine to run *desired* developers.

        The single clearest example of A15 in the product: this starts nothing.
        It writes a number the host reads on its next heartbeat and reconciles to
        by starting or stopping containers **itself**. "Run three developers in
        Berlin" is a piece of state the machine pulls, not a command pushed to it.
        """
        actor = self._require_principal(by)
        if desired < 0:
            raise SupervisorError("desired worker count cannot be negative")
        host = self._hosts.scale(workspace, host_id, desired)
        logger.info(
            f"supervisor: {actor} → host '{host_id}' desired_workers={desired} "
            f"(the host reconciles on its next heartbeat)"
        )
        return SupervisoryAct(
            act="host.scale", target=host_id, by=actor,
            detail={"desired_workers": host.desired_workers,
                    "running": len(host.workers)},
        )

    # -- dispatch -----------------------------------------------------------

    async def dispatch(
        self, workspace: str, *, by: str, hosts: Optional[List[str]] = None,
        workers_per_host: int = 1,
    ) -> Dict[str, Any]:
        """Put N developers to work across the fleet.

        Dispatch is **scaling plus ordering**, and neither half reaches out. The
        machines are asked (by state) to run more developers; the queue is
        ordered so that when those developers wake and claim, they claim the
        right thing.

        It does **not** claim on anyone's behalf. Claiming keeps its lock and its
        `touches` collision rule, because a race there is invisible until it
        corrupts work — and the seat has no business being the one exception.
        """
        actor = self._require_principal(by)
        if workers_per_host < 0:
            raise SupervisorError("workers per host cannot be negative")

        targets = hosts if hosts is not None else [
            h["id"] for h in self._hosts.list(workspace)
            if h.get("control") == "run"
        ]
        if not targets:
            raise SupervisorError(
                "no dev host is available to dispatch to — a host registers "
                "itself and heartbeats; the server cannot reach out to add one"
            )

        scaled = []
        for host_id in targets:
            act = await self.scale_host(
                workspace, host_id, workers_per_host, by=actor)
            scaled.append(act.to_dict())

        return {
            "workspace": workspace,
            "by": actor,
            "hosts": scaled,
            "requested_workers": workers_per_host * len(targets),
            "queue": self.ready_queue(workspace),
            # Stated in the response because it is the thing most likely to be
            # misread: nothing has started yet, and that is correct.
            "reaches_fleet_via": "heartbeat",
            "note": ("Recorded as intent. Each host starts or stops developers "
                     "itself when it next heartbeats — the server never dials a "
                     "host (A15)."),
        }

    def ready_queue(self, workspace: str) -> List[Dict[str, Any]]:
        """The tasks a waking worker should claim, best first.

        **Delegated to `WeaveCoordinator.ready()`, not reimplemented here.** I
        started to write the ordering out — pending, dependencies done, priority
        then id — and it was subtly wrong: the coordinator also excludes tasks
        whose `touches` overlap work already in progress. A queue that ignored
        that would hand two workers colliding tasks and let the claim lock refuse
        the second, turning a clean ordering problem into a race the fleet has to
        lose before it learns.

        So the seat asks the component that owns the rule (R10), which also keeps
        the `touches` collision logic in exactly one place — it is a named
        tripwire, and the way to respect it is to not grow a second copy.

        Deterministic either way (A12): same graph, same queue, no model.
        """
        if self._coordinator is None:
            return []
        return [
            {"id": t.id, "title": t.title, "priority": t.priority,
             "touches": list(t.touches)}
            for t in self._coordinator.ready(workspace)
        ]

    # -- what the board shows ----------------------------------------------

    def fleet(self, workspace: str) -> Dict[str, Any]:
        """Hosts and workers, with the gap a supervisor acts on.

        `desired` versus `running` per host is the number that matters: it is the
        difference between what the team asked for and what the machine has
        actually reconciled to, and a host that never closes it is one whose
        daemon is not heartbeating.
        """
        hosts = self._hosts.list(workspace)
        workers = self._workers.list(workspace)
        by_host: Dict[str, List[Dict[str, Any]]] = {}
        for worker in workers:
            by_host.setdefault(worker.get("host") or "", []).append(worker)

        return {
            "workspace": workspace,
            "hosts": [
                {
                    **host,
                    "running": len(by_host.get(host["id"], [])),
                    "reconciled": len(by_host.get(host["id"], []))
                    == host.get("desired_workers", 0),
                    "workers": by_host.get(host["id"], []),
                }
                for host in hosts
            ],
            "unassigned_workers": by_host.get("", []),
        }
