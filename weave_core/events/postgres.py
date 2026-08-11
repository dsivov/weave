"""The PostgreSQL ``LISTEN/NOTIFY`` ``EventBus`` adapter — the multi-worker one (A7).

The in-process bus cannot fan out across gunicorn workers: a subscriber in worker
2 never sees an event published in worker 1, with no error and no log. That is
the failure A7 exists to prevent, and this is the adapter that prevents it — the
database is already shared by every worker, so it is the fan-out point that needs
no new service (a broker would breach **A1**, and a new client library **A11**).

**No new dependency.** ``asyncpg`` is already the PostgreSQL driver, and the
connection settings come from :func:`weave_core.store.postgres.connection_settings`
rather than a second configuration path, so a deployment configures its database
once.

Three properties this adapter has to get right, because each fails *silently* if
it does not:

1. **Reconnection.** A dropped listener connection means the board simply stops
   updating — no exception reaches a subscriber, because subscribers are
   callbacks. So the listener reconnects with backoff and says so in the log, and
   :attr:`PostgresEventBus.connected` lets a health check see the truth.
2. **Payload size.** ``NOTIFY`` caps a payload at 8000 bytes; PostgreSQL raises
   above that, and a bus that swallowed the error would drop the event. Oversized
   events raise :class:`EventTooLarge` at the *publisher*, where the offending
   payload actually is.
3. **Self-delivery.** A publisher's own process must receive what it published,
   exactly like the in-process bus, or a single-worker deployment behaves
   differently from a multi-worker one and the adapter stops being swappable.
   PostgreSQL delivers ``NOTIFY`` to listeners on the same connection *and* other
   sessions, so this comes free — asserted in the tests rather than assumed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from weave_core.events.bus import WILDCARD, Handler
from weave_core.events.schema import Event
from weave_core.utils import logger

#: PostgreSQL's hard limit on a NOTIFY payload. Not tunable — it is compiled in.
MAX_NOTIFY_PAYLOAD = 8000

#: The channel every Weave event travels on. A single channel keeps LISTEN
#: bookkeeping trivial; the workspace is inside the event, and subscribers filter
#: on it exactly as they do on the in-process bus.
DEFAULT_CHANNEL = "weave_events"

#: Reconnection backoff, in seconds. Capped so a long outage does not turn into a
#: long silence after the database returns.
_BACKOFF_START = 0.5
_BACKOFF_MAX = 10.0


class EventTooLarge(ValueError):
    """An event whose serialised form exceeds PostgreSQL's NOTIFY limit.

    Raised at publish time on purpose. The alternative — dropping it, or
    truncating it — would make the bus lossy in a way no subscriber could
    detect, which is the class of failure this whole adapter exists to remove.
    """


class PostgresEventBus:
    """``EventBus`` over ``LISTEN``/``NOTIFY``. Satisfies the same two-method port.

    One long-lived connection listens; publishes borrow a connection from a small
    pool. Both are lazy, so constructing the bus opens nothing and a test or a
    CLI can build one without a database.
    """

    def __init__(
        self,
        *,
        settings: Optional[Dict[str, Any]] = None,
        channel: str = DEFAULT_CHANNEL,
        min_size: int = 1,
        max_size: int = 4,
    ) -> None:
        from weave_core.store.postgres import connection_settings

        self._settings = settings or connection_settings()
        self._channel = channel
        self._min_size = min_size
        self._max_size = max_size

        self._subs: Dict[str, List[Handler]] = {}
        self._pool = None
        self._listener_conn = None
        self._listener_task: Optional[asyncio.Task] = None
        self._closing = False
        self.connected = False

    # -- the port -----------------------------------------------------------

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subs.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        payload = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_NOTIFY_PAYLOAD:
            raise EventTooLarge(
                f"event '{event.type}' serialises to {len(encoded)} bytes, over "
                f"PostgreSQL's {MAX_NOTIFY_PAYLOAD}-byte NOTIFY limit. Live "
                "events carry a reference, not a document — put the body behind "
                "a locator and publish the id."
            )

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # $1 as a bound parameter would be interpolated into the channel
            # name; pg_notify takes both as values instead.
            await conn.execute("SELECT pg_notify($1, $2)", self._channel, payload)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Begin listening. Idempotent, so a lifespan may call it freely."""
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._closing = False
        self._listener_task = asyncio.create_task(self._listen_forever())

    async def close(self) -> None:
        self._closing = True
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._listener_task = None
        await self._drop_listener()
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        self.connected = False

    # -- internals ----------------------------------------------------------

    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                min_size=self._min_size, max_size=self._max_size, **self._settings
            )
            logger.info(
                "event bus: connected to PostgreSQL "
                f"{self._settings.get('host')}:{self._settings.get('port')} "
                f"(channel {self._channel})"
            )
        return self._pool

    async def _drop_listener(self) -> None:
        if self._listener_conn is not None:
            try:
                await self._listener_conn.close()
            except Exception:  # noqa: BLE001 - already going away
                pass
            self._listener_conn = None

    async def _listen_forever(self) -> None:
        """Hold a LISTEN connection open, reconnecting for as long as we are up.

        A listener that dies quietly is indistinguishable from a quiet system —
        the board just stops updating — so every failure is logged and every
        recovery is logged too, and `connected` reflects reality for a health
        check.
        """
        import asyncpg

        backoff = _BACKOFF_START
        while not self._closing:
            try:
                self._listener_conn = await asyncpg.connect(**self._settings)
                await self._listener_conn.add_listener(self._channel, self._on_notify)
                self.connected = True
                logger.info(f"event bus: listening on '{self._channel}'")
                backoff = _BACKOFF_START

                # Hold the connection open. `add_listener` dispatches from
                # asyncpg's own reader task; this loop only watches for the
                # connection going away.
                while not self._closing and not self._listener_conn.is_closed():
                    await asyncio.sleep(0.5)

                if not self._closing:
                    raise ConnectionError("listener connection closed by the server")

            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - reconnect on anything
                self.connected = False
                await self._drop_listener()
                if self._closing:
                    return
                logger.warning(
                    f"event bus: listener lost ({e}); reconnecting in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

        self.connected = False

    def _on_notify(self, connection, pid, channel, payload) -> None:
        """asyncpg calls this synchronously; dispatch runs as a task.

        A handler that raises must not take the listener down with it, or one bad
        subscriber silences every other subscriber in the process.
        """
        try:
            event = Event.from_dict(json.loads(payload))
        except (ValueError, TypeError) as e:
            logger.warning(f"event bus: undecodable notification dropped: {e}")
            return
        asyncio.create_task(self._dispatch(event))

    async def _dispatch(self, event: Event) -> None:
        handlers = list(self._subs.get(event.type, [])) + list(
            self._subs.get(WILDCARD, [])
        )
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:  # noqa: BLE001 - one subscriber, not all
                logger.error(f"event bus: handler for '{event.type}' raised: {e}")
