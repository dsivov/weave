"""The live surface: SSE filtering, presence, and the boundary that outlives a connection.

A stream is a long-lived subscription to a bus carrying **every** workspace's
traffic. That makes it the one place where a tenant check run *once* would be a
guard later events walk straight past — watch item W4's exact shape, and the
reason both checks here run per event rather than per connection:

1. the event's workspace matches the connection's;
2. the subscriber **still** has membership.

The second is the one that is easy to miss. A connection outlives a revocation,
so membership removed while someone holds a stream open has to stop that stream —
otherwise revocation quietly means "applies at the next page load", which is not
what anyone revoking access believes they are doing.
"""

from __future__ import annotations

import asyncio

import pytest

from weave.live.presence import PRESENCE_TTL, Presence, PresenceRegistry
from weave.live.stream import EventStream, presence_event, sse_frame
from weave_core.events.inprocess import InProcessBus
from weave_core.events.schema import Event

pytestmark = pytest.mark.offline


def _event(workspace: str = "alpha", type_: str = "task.claimed", **payload) -> Event:
    return Event(type=type_, payload=payload or {"task": "T-1"}, workspace=workspace)


async def _drain(stream: EventStream, expected: int, timeout: float = 1.0) -> list:
    """Collect *expected* data frames, ignoring comments."""
    frames = []
    gen = stream.frames()

    async def pump():
        async for frame in gen:
            if frame.startswith(":"):
                continue
            frames.append(frame)
            if len(frames) >= expected:
                return

    try:
        await asyncio.wait_for(pump(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await gen.aclose()
    return frames


# ── the tenant boundary, per event ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stream_receives_only_its_own_workspaces_events():
    stream = EventStream("alpha")

    await stream.on_event(_event("alpha", task="mine"))
    await stream.on_event(_event("beta", task="theirs"))
    await stream.on_event(_event("alpha", task="mine-too"))

    frames = await _drain(stream, expected=2)
    assert len(frames) == 2, "another workspace's event reached the stream"
    assert "theirs" not in "".join(frames)


@pytest.mark.asyncio
async def test_an_event_with_no_workspace_is_treated_as_default_not_as_everyones():
    """A missing workspace must not act as a wildcard. Defaulting the *event* to
    "default" is right; treating it as "matches every stream" would make any
    unlabelled publish a cross-tenant leak."""
    to_default = EventStream("default")
    to_alpha = EventStream("alpha")

    unlabelled = Event(type="task.claimed", payload={"task": "T"}, workspace="")
    await to_default.on_event(unlabelled)
    await to_alpha.on_event(unlabelled)

    assert len(await _drain(to_default, 1)) == 1
    assert await _drain(to_alpha, 1, timeout=0.2) == []


@pytest.mark.asyncio
async def test_revoking_membership_stops_a_stream_that_is_already_open():
    """The check that has to run per event rather than per connection.

    Without it, revocation means "applies at the next page load" — and the
    person you revoked is the one still holding the connection.
    """
    allowed = {"alpha"}
    stream = EventStream("alpha", may_access=lambda ws: ws in allowed)

    await stream.on_event(_event("alpha", task="before"))
    assert len(await _drain(stream, 1)) == 1

    allowed.clear()  # membership revoked mid-stream
    await stream.on_event(_event("alpha", task="after"))

    assert await _drain(stream, 1, timeout=0.2) == [], (
        "an event was delivered after access was revoked"
    )


@pytest.mark.asyncio
async def test_a_closed_stream_accepts_nothing_further():
    stream = EventStream("alpha")
    stream.close()
    await stream.on_event(_event("alpha"))
    assert await _drain(stream, 1, timeout=0.2) == []


# ── a slow client is bounded, and says so ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_slow_client_drops_the_oldest_rather_than_growing_without_bound():
    """One stalled browser must not become the server's memory problem. Dropping
    the oldest keeps a board *current* rather than correct-but-behind, which is
    the right trade for a board — and the count means "why did I miss an update"
    has an answer."""
    stream = EventStream("alpha", max_queued=3)

    for i in range(6):
        await stream.on_event(_event("alpha", task=f"T-{i}"))

    assert stream.dropped == 3
    frames = await _drain(stream, expected=3)
    joined = "".join(frames)
    assert "T-5" in joined, "the newest event was lost"
    assert "T-0" not in joined, "the oldest event survived a full queue"


@pytest.mark.asyncio
async def test_a_handler_never_raises_into_the_bus():
    """The bus dispatches to callbacks; a subscriber that raises would silence
    every other subscriber in the process."""
    stream = EventStream("alpha", max_queued=1)
    for _ in range(10):
        await stream.on_event(_event("alpha"))  # must not raise


# ── the wire format ──────────────────────────────────────────────────────────


def test_a_frame_carries_the_type_the_data_and_a_dedupe_id():
    """`id` lets a reconnecting client tell a re-delivery from a new event. The
    bus is not durable, so reconnect-after-a-gap is the ordinary case."""
    frame = sse_frame(_event("alpha", task="T-1"))

    assert frame.startswith("id: ")
    assert "\nevent: task.claimed\n" in frame
    assert '"task": "T-1"' in frame or '"task":"T-1"' in frame
    assert frame.endswith("\n\n"), "an SSE frame must end with a blank line"


@pytest.mark.asyncio
async def test_the_stream_opens_with_a_comment_so_the_client_knows_it_is_live():
    stream = EventStream("alpha")
    gen = stream.frames()
    first = await gen.__anext__()
    await gen.aclose()
    assert first.startswith(":")


@pytest.mark.asyncio
async def test_an_idle_stream_emits_keep_alives():
    """Proxies close idle connections, and a silently dropped stream looks
    exactly like a quiet system — the failure this phase is about."""
    stream = EventStream("alpha", heartbeat=0.05)
    gen = stream.frames()
    await gen.__anext__()                      # the open comment
    second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    await gen.aclose()
    assert second.startswith(":")


# ── it is a transport, not an answer surface (A9) ────────────────────────────


@pytest.mark.asyncio
async def test_the_stream_writes_what_was_published_and_computes_nothing():
    """A9: SSE is a third adapter, not a fourth answer surface. If a board could
    show something `/ask` would deny, the surfaces have diverged."""
    bus = InProcessBus()
    stream = EventStream("alpha")
    stream.subscribe_to(bus)

    published = _event("alpha", task="T-1", extra="carried through")
    await bus.publish(published)

    (frame,) = await _drain(stream, expected=1)
    assert "carried through" in frame
    assert published.dedupe_key() in frame


# ── presence ─────────────────────────────────────────────────────────────────


def test_presence_expires_rather_than_lingering():
    """Presence that outlives the person is worse than none: a board showing
    people who left teaches its readers to ignore it."""
    clock = {"t": 1000.0}
    registry = PresenceRegistry(ttl=10.0, now=lambda: clock["t"])

    registry.touch("alpha", "alice", board="B-1")
    assert [p.user for p in registry.on_board("alpha")] == ["alice"]

    clock["t"] += 11.0
    assert registry.on_board("alpha") == []


def test_presence_is_scoped_to_a_workspace():
    """"Who else is here" leaking across workspaces would expose colleagues,
    board names and artifact ids to another tenant."""
    registry = PresenceRegistry()
    registry.touch("alpha", "alice", board="B-1", editing="RFC-1")
    registry.touch("beta", "bob", board="B-1", editing="RFC-1")

    assert [p.user for p in registry.on_board("alpha")] == ["alice"]
    assert [p.user for p in registry.on_board("beta")] == ["bob"]
    assert registry.editing("alpha", "RFC-1")[0].user == "alice"


def test_a_heartbeat_refreshes_rather_than_duplicates():
    clock = {"t": 1000.0}
    registry = PresenceRegistry(ttl=10.0, now=lambda: clock["t"])

    registry.touch("alpha", "alice", board="B-1")
    clock["t"] += 5.0
    registry.touch("alpha", "alice", board="B-2")

    people = registry.on_board("alpha")
    assert len(people) == 1 and people[0].board == "B-2"


def test_presence_learned_from_the_bus_is_absorbed():
    """Behind several workers each process sees only its own clients, so the
    board is assembled from what the bus reports (A7)."""
    registry = PresenceRegistry(now=lambda: 2000.0)
    remote = Presence(user="bob", workspace="alpha", board="B-1", at=2000.0)

    registry.apply(remote)

    assert [p.user for p in registry.on_board("alpha")] == ["bob"]


def test_an_out_of_order_presence_event_does_not_resurrect_a_stale_position():
    """Events can arrive out of order; a stale one must not overwrite a newer."""
    registry = PresenceRegistry(now=lambda: 2000.0)
    registry.apply(Presence(user="bob", workspace="alpha", board="B-2", at=2000.0))
    registry.apply(Presence(user="bob", workspace="alpha", board="B-1", at=1000.0))

    assert registry.on_board("alpha")[0].board == "B-2"


def test_a_presence_change_becomes_an_event_on_the_right_workspace():
    entry = Presence(user="alice", workspace="alpha", board="B-1", at=1.0)
    event = presence_event(entry)

    assert event.workspace == "alpha"
    assert event.payload["user"] == "alice"
    assert Presence.from_dict(event.payload) == entry


def test_leaving_removes_the_entry():
    registry = PresenceRegistry()
    registry.touch("alpha", "alice")
    assert registry.leave("alpha", "alice") is True
    assert registry.on_board("alpha") == []
    assert registry.leave("alpha", "alice") is False


def test_the_default_ttl_is_longer_than_one_missed_heartbeat():
    """Long enough to ride out a missed beat, short enough that a closed laptop
    disappears while someone is still looking at the board."""
    assert 30.0 <= PRESENCE_TTL <= 120.0
