"""P3 — the worker registry: fleet presence + pause/resume/stop control-state.

A worker registers, heartbeats, and reads its control-state back on each
heartbeat (that's how a supervisor's pause/stop reaches an outbound-only loop).
A stale heartbeat reads offline without destroying the stored control-state; a
stopped worker is terminal.
"""

from __future__ import annotations

import pytest

from weave.team.workers import (
    HEARTBEAT_TTL, InMemoryWeaveWorkerStore, WorkerOwnershipError, WorkerRegistry,
)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _registry(clock=None):
    return WorkerRegistry(InMemoryWeaveWorkerStore(), now=clock or _Clock())


@pytest.mark.offline
@pytest.mark.asyncio
async def test_register_puts_worker_in_the_fleet():
    r = _registry()
    w = await r.register("w", "dev-1", role="developer", host="box-1",
                         capabilities=["python"], goal="build auth")
    assert w.status == "active" and w.control == "run"
    fleet = r.list("w")
    assert [v["id"] for v in fleet] == ["dev-1"]
    assert fleet[0]["role"] == "developer" and fleet[0]["goal"] == "build auth"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_heartbeat_returns_control_state():
    r = _registry()
    await r.register("w", "dev-1")
    hb = r.heartbeat("w", "dev-1", current_task="t1")
    assert hb["control"] == "run" and hb["status"] == "active"
    assert hb["current_task"] == "t1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_pause_resume_stop_ride_back_on_heartbeat():
    r = _registry()
    await r.register("w", "dev-1")
    r.set_control("w", "dev-1", "pause")
    assert r.heartbeat("w", "dev-1")["control"] == "pause"
    r.set_control("w", "dev-1", "resume")
    assert r.heartbeat("w", "dev-1")["control"] == "run"
    r.set_control("w", "dev-1", "stop")
    hb = r.heartbeat("w", "dev-1")
    assert hb["control"] == "stop" and hb["status"] == "stopped"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_stop_is_terminal():
    r = _registry()
    await r.register("w", "dev-1")
    r.set_control("w", "dev-1", "stop")
    with pytest.raises(ValueError):
        r.set_control("w", "dev-1", "resume")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_reregister_does_not_revive_a_stopped_worker():
    # a restarting container re-registers on start; it must NOT undo a supervisor's stop
    r = _registry()
    await r.register("w", "dev-1", role="developer", owner="bo")
    r.set_control("w", "dev-1", "stop")
    w = await r.register("w", "dev-1", role="developer", owner="bo")
    assert w.control == "stop" and w.status == "stopped"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_worker_is_bound_to_its_owner():
    r = _registry()
    await r.register("w", "dev-1", owner="bo")
    # another principal cannot re-register or heartbeat bo's worker id
    with pytest.raises(WorkerOwnershipError):
        await r.register("w", "dev-1", owner="cy")
    with pytest.raises(WorkerOwnershipError):
        r.heartbeat("w", "dev-1", owner="cy")
    # the owner still can
    assert r.heartbeat("w", "dev-1", owner="bo")["control"] == "run"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_stale_heartbeat_reads_offline_without_losing_control_state():
    clock = _Clock()
    r = _registry(clock)
    await r.register("w", "dev-1")
    r.set_control("w", "dev-1", "pause")            # stored control = pause
    clock.t += HEARTBEAT_TTL + 10                   # go silent past the TTL
    v = r.get("w", "dev-1")
    assert v["status"] == "offline" and v["stale"] is True
    assert v["control"] == "pause"                  # stored state intact
    # a fresh heartbeat brings it back live
    clock.t += 1
    r.heartbeat("w", "dev-1")
    assert r.get("w", "dev-1")["status"] == "paused"
    # exclude offline from the fleet view
    clock.t += HEARTBEAT_TTL + 10
    assert r.list("w", include_offline=False) == []


@pytest.mark.offline
def test_unknown_worker_raises():
    r = _registry()
    with pytest.raises(KeyError):
        r.heartbeat("w", "ghost")
    with pytest.raises(KeyError):
        r.set_control("w", "ghost", "pause")
