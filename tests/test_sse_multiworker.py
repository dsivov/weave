"""An event published in one worker reaches a client in another (A7, D-019).

This is the failure A7 exists to prevent, and it is silent: put the live surface
on the in-process bus behind gunicorn `--workers 2`, and a client connected to
worker 2 never receives an event published on worker 1. Nothing raises, nothing
logs, the board simply stops updating — for some users and not others, which is
the hardest kind of bug to be told about.

**The asymmetry is the point.** Cross-process delivery must *work* on the
PostgreSQL adapter and must *not* work on the in-process one. A test that only
asserted the first would pass against a bus that delivered nothing at all, and a
test that only asserted the second would pass against a broken adapter. Both are
asserted, and the in-process case is a positive assertion of absence rather than
a skip.

Real subprocesses, not threads or mocks: the whole claim is about separate
address spaces, and threads share one. `multiprocessing` with the `spawn` start
method is what gunicorn's fork model comes down to for this purpose.
"""

from __future__ import annotations

import multiprocessing as mp
import os

import pytest

from weave.server.config import (
    EVENT_BUSES,
    IN_PROCESS_BUS,
    POSTGRES_BUS,
    BusDeploymentMismatch,
    assert_bus_matches_deployment,
)
from weave_core.events.schema import Event

POSTGRES_VARS = (
    "WEAVE_POSTGRES_HOST",
    "WEAVE_POSTGRES_USER",
    "WEAVE_POSTGRES_PASSWORD",
    "WEAVE_POSTGRES_DATABASE",
)
postgres_configured = all(os.environ.get(v) for v in POSTGRES_VARS)
requires_postgres = pytest.mark.skipif(
    not postgres_configured,
    reason=(
        "PostgreSQL is not configured, so cross-worker fan-out (A7, the property "
        "the whole live surface rests on) is unverified in this run — set "
        + ", ".join(POSTGRES_VARS)
    ),
)

CHANNEL = "weave_events_test"


# ── half one of A7: the refusal ──────────────────────────────────────────────


@pytest.mark.offline
def test_multi_worker_on_the_in_process_bus_is_refused():
    """Ships in the same commit as the adapter. An adapter that removes a silent
    failure, plus a configuration that still permits it, is not a fix."""
    with pytest.raises(BusDeploymentMismatch) as exc:
        assert_bus_matches_deployment(IN_PROCESS_BUS, workers=2)

    message = str(exc.value)
    assert "WEAVE_EVENT_BUS=postgres" in message, "the fix must be in the message"
    assert "--workers 1" in message, "the other fix must be there too"


@pytest.mark.offline
@pytest.mark.parametrize("workers", [1, 2, 8])
def test_the_postgres_bus_is_allowed_at_any_worker_count(workers):
    """The check must not run the other way. PostgreSQL with one worker is
    unnecessary, not wrong, and refusing it would break the ordinary case of a
    single worker against a production database."""
    assert_bus_matches_deployment(POSTGRES_BUS, workers=workers)


@pytest.mark.offline
def test_a_single_worker_may_use_the_in_process_bus():
    assert_bus_matches_deployment(IN_PROCESS_BUS, workers=1)


@pytest.mark.offline
def test_an_unknown_bus_is_refused_rather_than_defaulted():
    """Falling back to a default would pick the in-process bus, which is the
    unsafe half of the pairing."""
    with pytest.raises(BusDeploymentMismatch):
        assert_bus_matches_deployment("rabbitmq", workers=1)


@pytest.mark.offline
def test_every_declared_bus_is_accepted_at_one_worker():
    """Guards the list against drifting away from the check."""
    for bus in EVENT_BUSES:
        assert_bus_matches_deployment(bus, workers=1)


# ── half two: the adapter actually fans out across processes ─────────────────


def _subscriber(settings: dict, ready, received, timeout: float = 20.0) -> None:
    """Run in a separate process: listen, then report what arrived."""
    import asyncio

    from weave_core.events.postgres import PostgresEventBus

    async def run():
        bus = PostgresEventBus(settings=settings, channel=CHANNEL)
        got = asyncio.Event()

        async def handler(event: Event) -> None:
            received.put(event.to_dict())
            got.set()

        bus.subscribe("task.claimed", handler)
        await bus.start()

        # Wait until LISTEN is actually established, or the publisher may
        # publish into a void and the test would flake rather than fail.
        for _ in range(int(timeout * 10)):
            if bus.connected:
                break
            await asyncio.sleep(0.1)
        ready.set()

        try:
            await asyncio.wait_for(got.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await bus.close()

    asyncio.run(run())


def _inprocess_subscriber(settings: dict, ready, received, timeout: float = 5.0) -> None:
    """The same shape on the in-process bus, which cannot possibly work."""
    import asyncio

    from weave_core.events.inprocess import InProcessBus

    async def run():
        bus = InProcessBus()
        got = asyncio.Event()

        async def handler(event: Event) -> None:
            received.put(event.to_dict())
            got.set()

        bus.subscribe("task.claimed", handler)
        ready.set()
        try:
            await asyncio.wait_for(got.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    asyncio.run(run())


def _publish(settings: dict, event: Event) -> None:
    import asyncio

    from weave_core.events.postgres import PostgresEventBus

    async def run():
        bus = PostgresEventBus(settings=settings, channel=CHANNEL)
        await bus.publish(event)
        await bus.close()

    asyncio.run(run())


def _publish_inprocess(settings: dict, event: Event) -> None:
    import asyncio

    from weave_core.events.inprocess import InProcessBus

    async def run():
        await InProcessBus().publish(event)

    asyncio.run(run())


EVENT = Event(
    type="task.claimed",
    payload={"task": "TASK-1", "by": "developer"},
    workspace="alpha",
    source="test",
)


def _run_two_workers(subscriber, publisher, settings, wait_ready: float, wait_msg: float):
    """Worker 1 publishes, worker 2 listens. Returns what worker 2 received."""
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    received = ctx.Queue()

    listener = ctx.Process(target=subscriber, args=(settings, ready, received))
    listener.start()
    try:
        assert ready.wait(timeout=wait_ready), "the subscriber never became ready"

        sender = ctx.Process(target=publisher, args=(settings, EVENT))
        sender.start()
        sender.join(timeout=wait_ready)

        listener.join(timeout=wait_msg)
        return None if received.empty() else received.get_nowait()
    finally:
        for proc in (listener,):
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)


@pytest.mark.integration
@requires_postgres
def test_an_event_published_in_one_worker_reaches_a_client_in_another():
    """The A7 property, across real process boundaries."""
    from weave_core.store.postgres import connection_settings

    got = _run_two_workers(
        _subscriber, _publish, connection_settings(), wait_ready=30, wait_msg=30
    )

    assert got is not None, (
        "worker 2 never received the event worker 1 published — this is exactly "
        "the silent fan-out failure A7 exists to prevent"
    )
    assert got["type"] == "task.claimed"
    assert got["payload"] == {"task": "TASK-1", "by": "developer"}
    assert got["workspace"] == "alpha"


@pytest.mark.offline
def test_the_in_process_bus_does_not_reach_another_worker():
    """The other half of the asymmetry, asserted rather than assumed.

    If this ever *passes* delivery, either the in-process bus grew a cross-process
    channel — which would be a new deployable and an A1 problem — or the test is
    no longer testing separate processes. Both are worth failing over.
    """
    got = _run_two_workers(
        _inprocess_subscriber, _publish_inprocess, {}, wait_ready=20, wait_msg=10
    )

    assert got is None, (
        "the in-process bus delivered across processes, which it cannot do — "
        "the test is no longer exercising separate address spaces"
    )
