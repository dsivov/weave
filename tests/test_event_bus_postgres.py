"""The PostgreSQL bus adapter's own failure modes — the ones that fail quietly.

Cross-process fan-out is asserted in `test_sse_multiworker.py`. This file covers
the three ways this adapter could be *subtly* wrong while still appearing to
work, each of which loses events with nothing to show for it:

1. an oversized payload (PostgreSQL caps `NOTIFY` at 8000 bytes);
2. a subscriber that raises, taking the listener down with it;
3. the adapter behaving differently from the in-process one, so that swapping
   adapters — which A7 requires deployments to do — changes behaviour.

Watch item W4 is the lens: a guard in one adapter protects only the callers who
arrive through it, so anything asserted of one bus should be asserted of both.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from weave_core.events.inprocess import InProcessBus
from weave_core.events.postgres import (
    MAX_NOTIFY_PAYLOAD,
    EventTooLarge,
    PostgresEventBus,
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
    reason="PostgreSQL is not configured — set " + ", ".join(POSTGRES_VARS),
)

CHANNEL = "weave_events_adapter_test"


def _settings():
    from weave_core.store.postgres import connection_settings

    return connection_settings()


# ── 1 · an oversized event is refused at the publisher, never dropped ────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_an_oversized_event_raises_rather_than_vanishing():
    """PostgreSQL rejects a NOTIFY payload over 8000 bytes. A bus that swallowed
    that error would be lossy in a way no subscriber could detect — the board
    would miss an update and nothing would say so.

    Raised at the publisher, because that is where the offending payload is and
    where someone can do something about it.
    """
    bus = PostgresEventBus(settings={"host": "unused"}, channel=CHANNEL)
    huge = Event(type="task.claimed", payload={"blob": "x" * (MAX_NOTIFY_PAYLOAD + 1)})

    with pytest.raises(EventTooLarge) as exc:
        await bus.publish(huge)

    message = str(exc.value)
    assert str(MAX_NOTIFY_PAYLOAD) in message
    assert "locator" in message, "the message should say what to do instead"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_size_check_runs_before_any_connection_is_opened():
    """Deliberately ordered: an unpublishable event must fail the same way
    whether or not a database is reachable, so the error a developer sees is
    about their payload and not about connectivity."""
    bus = PostgresEventBus(
        settings={"host": "203.0.113.1", "port": 5432, "user": "x",
                  "password": "x", "database": "x"},
        channel=CHANNEL,
    )
    with pytest.raises(EventTooLarge):
        await bus.publish(
            Event(type="t", payload={"blob": "x" * (MAX_NOTIFY_PAYLOAD + 1)})
        )


# ── 2 · one bad subscriber does not silence the others ───────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_raising_handler_does_not_stop_the_others():
    """On a callback bus there is nobody to propagate an exception *to*, so a
    handler that raises would otherwise take down delivery for every other
    subscriber in the process — silently, again."""
    bus = PostgresEventBus(settings={"host": "unused"}, channel=CHANNEL)
    delivered = []

    async def bad(event: Event) -> None:
        raise RuntimeError("this subscriber is broken")

    async def good(event: Event) -> None:
        delivered.append(event.type)

    bus.subscribe("task.claimed", bad)
    bus.subscribe("task.claimed", good)

    await bus._dispatch(Event(type="task.claimed"))

    assert delivered == ["task.claimed"], (
        "a raising subscriber prevented a healthy one from receiving the event"
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_an_undecodable_notification_is_dropped_not_fatal():
    """A malformed payload on the channel — anything else NOTIFYing there — must
    not kill the listener, or one stray message ends live updates for good."""
    bus = PostgresEventBus(settings={"host": "unused"}, channel=CHANNEL)
    bus.subscribe("*", lambda e: asyncio.sleep(0))

    bus._on_notify(None, 0, CHANNEL, "not json at all")  # must not raise


# ── 3 · the two adapters behave the same where it matters ────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("bus_factory", [
    lambda: InProcessBus(),
    lambda: PostgresEventBus(settings={"host": "unused"}, channel=CHANNEL),
], ids=["inprocess", "postgres"])
async def test_exact_type_and_wildcard_handlers_both_run_on_either_adapter(bus_factory):
    """A7 requires a deployment to swap adapters. If dispatch differed between
    them, the swap would change behaviour — and the deployment that changed would
    be the production one."""
    bus = bus_factory()
    seen = []

    async def exact(event: Event) -> None:
        seen.append("exact")

    async def wildcard(event: Event) -> None:
        seen.append("wildcard")

    bus.subscribe("task.claimed", exact)
    bus.subscribe("*", wildcard)

    event = Event(type="task.claimed")
    if isinstance(bus, InProcessBus):
        await bus.publish(event)
    else:
        await bus._dispatch(event)

    assert seen == ["exact", "wildcard"], (
        "exact-type handlers run before wildcards, on both adapters"
    )


@pytest.mark.offline
def test_both_adapters_satisfy_the_same_port():
    """The port is two methods; a deployment selects an adapter by configuration
    and must not need to know which one it got."""
    from weave_core.events.bus import EventBus

    assert isinstance(InProcessBus(), EventBus)
    assert isinstance(
        PostgresEventBus(settings={"host": "unused"}, channel=CHANNEL), EventBus
    )


# ── against a real database ──────────────────────────────────────────────────


@pytest.mark.integration
@requires_postgres
@pytest.mark.asyncio
async def test_a_publisher_receives_its_own_event():
    """Self-delivery, which the in-process bus gives for free.

    Without it a single-worker deployment on the PostgreSQL bus would behave
    differently from the same deployment on the in-process bus — and A7 exists
    precisely so that swapping adapters is safe.
    """
    bus = PostgresEventBus(settings=_settings(), channel=CHANNEL)
    got = asyncio.Event()
    received = []

    async def handler(event: Event) -> None:
        received.append(event)
        got.set()

    bus.subscribe("task.claimed", handler)
    await bus.start()
    try:
        for _ in range(200):
            if bus.connected:
                break
            await asyncio.sleep(0.05)
        assert bus.connected, "the listener never connected"

        await bus.publish(Event(type="task.claimed", payload={"task": "T-1"},
                                workspace="alpha"))
        await asyncio.wait_for(got.wait(), timeout=15)
    finally:
        await bus.close()

    assert received and received[0].payload == {"task": "T-1"}
    assert received[0].workspace == "alpha"


@pytest.mark.integration
@requires_postgres
@pytest.mark.asyncio
async def test_connected_reports_the_truth_for_a_health_check():
    """A listener that dies quietly looks exactly like a quiet system, so the
    flag a health check reads has to mean something."""
    bus = PostgresEventBus(settings=_settings(), channel=CHANNEL)
    assert bus.connected is False, "connected before starting"

    await bus.start()
    for _ in range(200):
        if bus.connected:
            break
        await asyncio.sleep(0.05)
    assert bus.connected is True

    await bus.close()
    assert bus.connected is False, "connected after close"
