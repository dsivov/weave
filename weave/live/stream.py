"""SSE — the transport by which bus events reach a browser.

`GET /live/stream` holds a connection open and writes events as they are
published. It is a **third adapter**, not a fourth answer surface (A9): it
answers no question of its own and computes nothing, so a board cannot show
something `/ask` or MCP would deny. Everything it writes was published by
whatever performed the action.

**A15 holds, and this is the shape that could look like it does not.** SSE is the
*client* holding a connection open to the hub. The server never dials anyone. A
dev host behind NAT is unaffected, because nothing here requires an inbound
connection to a host or a worker.

**The tenant boundary is enforced per event, not per connection.** A stream is a
long-lived subscription to a bus carrying every workspace's traffic, so a filter
that ran once at connect time would be a guard that later events walk past —
exactly the shape watch item W4 names. Two checks run on **every** event before
it is written:

1. the event's workspace matches the connection's workspace;
2. the subscriber still has membership of that workspace.

The second matters because a connection outlives a revocation. Membership removed
while someone holds a stream open must stop the stream, or revocation means
"stops applying at the next reload".
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, Optional

from weave_core.events.bus import WILDCARD
from weave_core.events.schema import Event
from weave_core.utils import logger

#: How often to write a comment frame when nothing else is flowing. Proxies and
#: load balancers close an idle connection, and a silently dropped stream looks
#: exactly like a quiet system — the failure mode this whole phase is about.
HEARTBEAT_SECONDS = 20.0

#: How many events may queue for one slow client before it is disconnected. An
#: unbounded queue turns one stalled browser into the server's memory problem.
MAX_QUEUED_EVENTS = 256


def sse_frame(event: Event) -> str:
    """One event, in the `text/event-stream` wire format.

    `id` carries the dedupe key so a reconnecting client can tell a
    re-delivery from a new event — the bus is not durable, and a reconnect after
    a gap is the ordinary case rather than the exceptional one.
    """
    data = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
    return f"id: {event.dedupe_key()}\nevent: {event.type}\ndata: {data}\n\n"


class EventStream:
    """One subscriber's view of the bus: filtered, bounded, and cancellable.

    Deliberately not a router. The filtering rules are the interesting part and
    they belong somewhere testable without HTTP — `weave/server/routers/live.py`
    is the thin adapter over this.
    """

    def __init__(
        self,
        workspace: str,
        *,
        may_access: Optional[Callable[[str], bool]] = None,
        max_queued: int = MAX_QUEUED_EVENTS,
        heartbeat: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._workspace = workspace
        # Re-checked per event rather than captured once: a connection outlives a
        # revocation, and membership removed mid-stream must take effect now.
        self._may_access = may_access or (lambda ws: True)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queued)
        self._heartbeat = heartbeat
        self._closed = False
        self.dropped = 0

    # -- the bus side -------------------------------------------------------

    async def on_event(self, event: Event) -> None:
        """Bus handler. Never raises: a subscriber that throws would take the
        listener down for every other subscriber in the process."""
        if self._closed:
            return
        if not self._admits(event):
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Dropping the oldest keeps a live board *current* rather than
            # correct-but-behind, which is the right trade for a board. It is
            # counted, so "why did I miss an update" has an answer.
            self.dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    def _admits(self, event: Event) -> bool:
        """Both halves of the tenant check, on every event."""
        if (event.workspace or "default") != self._workspace:
            return False
        if not self._may_access(self._workspace):
            logger.info(
                f"live stream: closing — access to '{self._workspace}' was revoked"
            )
            self._closed = True
            return False
        return True

    # -- the client side ----------------------------------------------------

    async def frames(self) -> AsyncIterator[str]:
        """Yield SSE frames until the client goes away or access is revoked."""
        yield ": stream open\n\n"
        while not self._closed:
            try:
                event = await asyncio.wait_for(self._queue.get(), self._heartbeat)
            except asyncio.TimeoutError:
                # A comment frame: keeps proxies from closing an idle connection,
                # and gives the client something to notice a dead link by.
                yield ": keep-alive\n\n"
                continue
            except asyncio.CancelledError:
                break
            yield sse_frame(event)

    def close(self) -> None:
        self._closed = True

    def subscribe_to(self, bus) -> None:
        """Attach to the bus. Wildcard, because a board shows everything it is
        entitled to see and the filtering that matters is the tenant check."""
        bus.subscribe(WILDCARD, self.on_event)


def presence_event(entry, *, source: str = "live") -> Event:
    """A presence change, as an event other workers learn from.

    Presence is per-process state, so behind several workers each one knows only
    its own clients unless the change travels on the bus — the same fan-out A7
    requires for everything else.
    """
    from weave.live.presence import PRESENCE_EVENT

    return Event(
        type=PRESENCE_EVENT,
        payload=entry.to_dict(),
        workspace=entry.workspace,
        source=source,
    )
