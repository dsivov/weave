"""The senior-developer seat: recorded intent, attributed, never a call outward.

Three properties, and each is a constraint rather than a feature:

- **A15** — the hub never dials out. Dispatch, pause, resume, redirect and scale
  all write state the fleet reads back on its next heartbeat. This is the phase
  most likely to break it, because every one of those *reads* like the server
  telling a worker what to do.
- **A6** — every act carries an authenticated principal, passed in by the router
  from the request identity. Nothing accepts an actor from a payload.
- **A12** — no model in the dispatch path. Ordering is deterministic graph logic,
  and the same graph gives the same queue.

The A15 test is the one worth reading: it asserts the *absence* of outbound
network calls by trapping the socket layer, because "we didn't write a POST" is
not something a normal test can see.
"""

from __future__ import annotations

import socket

import pytest

from weave.devhost.registry import DevHostRegistry, InMemoryDevHostStore
from weave.team.coordinator import WeaveCoordinator
from weave.team.store import InMemoryWeaveTaskStore, WeaveTask
from weave.team.supervisor import (
    HOST_ACTIONS,
    WORKER_ACTIONS,
    NotAuthenticated,
    Supervisor,
    SupervisorError,
)
from weave.team.workers import InMemoryWeaveWorkerStore, WorkerRegistry

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"
SENIOR = "dana"


@pytest.fixture
def seat():
    workers = WorkerRegistry(InMemoryWeaveWorkerStore())
    hosts = DevHostRegistry(InMemoryDevHostStore(), worker_registry=workers)
    tasks = InMemoryWeaveTaskStore()
    coordinator = WeaveCoordinator(tasks)
    return Supervisor(workers, hosts, coordinator), workers, hosts, coordinator


async def _host(hosts, host_id="berlin", **kw):
    return await hosts.register(WORKSPACE, host_id, machine=host_id, owner="dana", **kw)


async def _worker(workers, worker_id="berlin-1", host="berlin"):
    return await workers.register(WORKSPACE, worker_id, host=host, owner="dana")


# ── A15: nothing dials out ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_supervisory_act_opens_an_outbound_connection(seat, monkeypatch):
    """The constraint this phase is most likely to break, asserted directly.

    Dispatch, control and scale all read like commands. If any of them ever
    grows a "just POST to the host" shortcut it will work on a laptop and break
    every deployment behind NAT — silently, for whoever tries one first. So the
    socket layer is trapped and the whole supervisory surface is exercised.
    """
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await _worker(workers)

    dialled = []
    real_connect = socket.socket.connect

    def _trap(self, address, *args, **kwargs):
        dialled.append(address)
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _trap)

    await supervisor.dispatch(WORKSPACE, by=SENIOR, workers_per_host=2)
    await supervisor.control_worker(WORKSPACE, "berlin-1", "pause", by=SENIOR)
    await supervisor.control_worker(WORKSPACE, "berlin-1", "resume", by=SENIOR)
    await supervisor.control_worker(
        WORKSPACE, "berlin-1", "redirect", by=SENIOR, goal="fix the flaky test")
    await supervisor.control_host(WORKSPACE, "berlin", "drain", by=SENIOR)
    await supervisor.scale_host(WORKSPACE, "berlin", 3, by=SENIOR)

    assert dialled == [], (
        "a supervisory act opened an outbound connection — the hub never dials "
        f"a host or worker (A15): {dialled}"
    )


@pytest.mark.asyncio
async def test_scaling_records_intent_and_starts_nothing(seat):
    """The clearest instance of A15 in the product. `desired_workers` is a number
    the machine pulls and reconciles to itself."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts)

    act = await supervisor.scale_host(WORKSPACE, "berlin", 3, by=SENIOR)

    assert act.detail["desired_workers"] == 3
    assert act.detail["running"] == 0, "scaling started something"
    assert act.reaches_fleet_via == "heartbeat"


@pytest.mark.asyncio
async def test_the_host_learns_the_intent_on_its_next_heartbeat(seat):
    """The other half: the state written above actually reaches the machine, and
    only by the machine asking."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await supervisor.scale_host(WORKSPACE, "berlin", 2, by=SENIOR)

    reply = hosts.heartbeat(WORKSPACE, "berlin", owner="dana")

    assert reply["desired_workers"] == 2
    assert reply["control"] == "run"


@pytest.mark.asyncio
async def test_a_drained_host_is_told_to_hold_nothing_new(seat):
    """`drain` means finish what you hold and claim nothing new, so the reconcile
    target goes to zero regardless of what was asked for while it was running."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await supervisor.scale_host(WORKSPACE, "berlin", 4, by=SENIOR)
    await supervisor.control_host(WORKSPACE, "berlin", "drain", by=SENIOR)

    assert hosts.heartbeat(WORKSPACE, "berlin", owner="dana")["desired_workers"] == 0


@pytest.mark.asyncio
async def test_a_paused_worker_is_named_to_its_host_so_it_is_not_restarted(seat):
    """Two control planes compose: the host count says *how many*, a per-worker
    pause says *not that one*. Without `held_workers` the machine would see a
    supervisor-paused container missing and dutifully restart it — silently
    undoing a person's decision."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await _worker(workers, "berlin-1")
    hosts.heartbeat(WORKSPACE, "berlin", workers=["berlin-1"], owner="dana")

    await supervisor.control_worker(WORKSPACE, "berlin-1", "pause", by=SENIOR)
    reply = hosts.heartbeat(WORKSPACE, "berlin", workers=["berlin-1"], owner="dana")

    assert "berlin-1" in reply["held_workers"]


# ── A6: attributed, never self-stamped ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda s: s.control_worker(WORKSPACE, "berlin-1", "pause", by=""),
    lambda s: s.control_host(WORKSPACE, "berlin", "drain", by=""),
    lambda s: s.scale_host(WORKSPACE, "berlin", 1, by=""),
    lambda s: s.dispatch(WORKSPACE, by="", workers_per_host=1),
])
async def test_every_supervisory_act_requires_a_principal(seat, call):
    """Refused rather than defaulted to "system". These are the acts most worth
    being able to ask about afterwards."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await _worker(workers)

    with pytest.raises(NotAuthenticated):
        await call(supervisor)


@pytest.mark.asyncio
async def test_the_act_carries_the_principal_that_performed_it(seat):
    supervisor, workers, hosts, _c = seat
    await _host(hosts)
    await _worker(workers)

    act = await supervisor.control_worker(WORKSPACE, "berlin-1", "pause", by=SENIOR)

    assert act.by == SENIOR
    assert act.act == "worker.pause"
    assert act.target == "berlin-1"


# ── the supervisory surface ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
async def test_worker_controls_land_on_the_record(seat, action):
    supervisor, workers, hosts, _c = seat
    await _worker(workers)

    act = await supervisor.control_worker(WORKSPACE, "berlin-1", action, by=SENIOR)

    expected = {"pause": "pause", "resume": "run", "stop": "stop"}[action]
    assert act.detail["control"] == expected
    assert workers.get(WORKSPACE, "berlin-1")["control"] == expected


@pytest.mark.asyncio
async def test_a_redirect_changes_the_goal_without_stopping_the_worker(seat):
    """Redirect is not a control state. The loop keeps running and reads its new
    instructions between steps, exactly as it reads a pause."""
    supervisor, workers, hosts, _c = seat
    await _worker(workers)

    act = await supervisor.control_worker(
        WORKSPACE, "berlin-1", "redirect", by=SENIOR, goal="fix the flaky test")

    assert act.detail["goal"] == "fix the flaky test"
    assert act.detail["control"] == "run", "a redirect must not pause the worker"


@pytest.mark.asyncio
async def test_a_redirect_needs_somewhere_to_redirect_to(seat):
    supervisor, workers, hosts, _c = seat
    await _worker(workers)

    with pytest.raises(SupervisorError):
        await supervisor.control_worker(WORKSPACE, "berlin-1", "redirect", by=SENIOR)


@pytest.mark.asyncio
async def test_a_stopped_worker_is_not_redirected(seat):
    """`stop` is terminal. Giving a stopped worker a new goal would leave a
    record implying it went and did something."""
    supervisor, workers, hosts, _c = seat
    await _worker(workers)
    await supervisor.control_worker(WORKSPACE, "berlin-1", "stop", by=SENIOR)

    with pytest.raises(ValueError):
        await supervisor.control_worker(
            WORKSPACE, "berlin-1", "redirect", by=SENIOR, goal="something else")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["explode", "", "PAUSE"])
async def test_an_unknown_action_is_refused_with_the_alternatives(seat, action):
    supervisor, workers, hosts, _c = seat
    await _worker(workers)

    with pytest.raises(SupervisorError) as exc:
        await supervisor.control_worker(WORKSPACE, "berlin-1", action, by=SENIOR)
    assert "pause" in str(exc.value)


@pytest.mark.offline
def test_the_declared_actions_are_the_ones_supervision_offers():
    assert set(WORKER_ACTIONS) == {"pause", "resume", "stop", "redirect"}
    assert set(HOST_ACTIONS) == {"run", "drain", "pause", "stop"}


# ── A12: deterministic ordering, and it defers to the claim rule ─────────────


@pytest.mark.asyncio
async def test_the_queue_is_ordered_by_priority_then_id(seat):
    supervisor, workers, hosts, coordinator = seat
    for tid, prio in [("T-3", "normal"), ("T-1", "critical"), ("T-2", "high")]:
        coordinator.store.save(WORKSPACE, WeaveTask(id=tid, title=tid, priority=prio))

    queue = supervisor.ready_queue(WORKSPACE)

    assert [t["id"] for t in queue] == ["T-1", "T-2", "T-3"]


@pytest.mark.asyncio
async def test_the_queue_is_the_same_every_time(seat):
    """A12: no model decides what matters, so the same graph gives the same
    queue — which is what makes a fleet's behaviour reproducible."""
    supervisor, workers, hosts, coordinator = seat
    for tid in ("T-1", "T-2", "T-3"):
        coordinator.store.save(WORKSPACE, WeaveTask(id=tid, title=tid))

    assert supervisor.ready_queue(WORKSPACE) == supervisor.ready_queue(WORKSPACE)


@pytest.mark.asyncio
async def test_the_queue_respects_the_touches_collision_rule(seat):
    """The reason `ready_queue` delegates rather than reimplements.

    A task whose `touches` overlap work already in progress is not offered. A
    hand-rolled ordering that got this wrong would hand two workers colliding
    tasks and let the claim lock refuse the second — turning an ordering problem
    into a race the fleet has to lose before it learns.
    """
    supervisor, workers, hosts, coordinator = seat
    coordinator.store.save(WORKSPACE, WeaveTask(
        id="T-1", title="in flight", status="in_progress", touches=["weave/server"]))
    coordinator.store.save(WORKSPACE, WeaveTask(
        id="T-2", title="collides", touches=["weave/server"]))
    coordinator.store.save(WORKSPACE, WeaveTask(
        id="T-3", title="independent", touches=["weave/live"]))

    assert [t["id"] for t in supervisor.ready_queue(WORKSPACE)] == ["T-3"]


@pytest.mark.asyncio
async def test_a_blocked_task_is_not_offered(seat):
    supervisor, workers, hosts, coordinator = seat
    coordinator.store.save(WORKSPACE, WeaveTask(id="T-1", title="dep", status="pending"))
    coordinator.store.save(WORKSPACE, WeaveTask(
        id="T-2", title="blocked", depends_on=["T-1"]))

    assert [t["id"] for t in supervisor.ready_queue(WORKSPACE)] == ["T-1"]


# ── dispatch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_scales_every_running_host_and_returns_the_queue(seat):
    supervisor, workers, hosts, coordinator = seat
    await _host(hosts, "berlin")
    await _host(hosts, "lisbon")
    coordinator.store.save(WORKSPACE, WeaveTask(id="T-1", title="work"))

    result = await supervisor.dispatch(WORKSPACE, by=SENIOR, workers_per_host=2)

    assert result["requested_workers"] == 4
    assert {h["target"] for h in result["hosts"]} == {"berlin", "lisbon"}
    assert [t["id"] for t in result["queue"]] == ["T-1"]
    assert result["reaches_fleet_via"] == "heartbeat"


@pytest.mark.asyncio
async def test_dispatch_says_so_when_there_is_no_host_to_dispatch_to(seat):
    """And the message names *why* the server cannot fix it: a host registers
    itself. There is no "add a host" the hub can perform."""
    supervisor, workers, hosts, _c = seat

    with pytest.raises(SupervisorError) as exc:
        await supervisor.dispatch(WORKSPACE, by=SENIOR, workers_per_host=1)
    assert "registers itself" in str(exc.value)


@pytest.mark.asyncio
async def test_dispatch_skips_a_stopped_host(seat):
    supervisor, workers, hosts, _c = seat
    await _host(hosts, "berlin")
    await _host(hosts, "lisbon")
    await supervisor.control_host(WORKSPACE, "lisbon", "stop", by=SENIOR)

    result = await supervisor.dispatch(WORKSPACE, by=SENIOR, workers_per_host=1)

    assert {h["target"] for h in result["hosts"]} == {"berlin"}


# ── the board's number ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_fleet_view_shows_desired_against_running(seat):
    """The gap a supervisor acts on: what the team asked for versus what the
    machine has actually reconciled to. A host that never closes it is one whose
    daemon is not heartbeating."""
    supervisor, workers, hosts, _c = seat
    await _host(hosts, "berlin")
    await _worker(workers, "berlin-1", host="berlin")
    await supervisor.scale_host(WORKSPACE, "berlin", 3, by=SENIOR)

    view = supervisor.fleet(WORKSPACE)
    berlin = next(h for h in view["hosts"] if h["id"] == "berlin")

    assert berlin["desired_workers"] == 3
    assert berlin["running"] == 1
    assert berlin["reconciled"] is False
