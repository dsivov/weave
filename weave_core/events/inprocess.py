"""The in-process ``EventBus`` adapter — correct for exactly one deployment (A7).

Single-process async pub/sub. Handlers registered for the event's exact type run
first, in registration order, then wildcard handlers.

**Use this adapter only in a single-process deployment.** It cannot fan out
across gunicorn workers: a subscriber in worker 2 will never see an event
published in worker 1, with no error and no log. Any multi-worker deployment uses
the PostgreSQL ``LISTEN/NOTIFY`` adapter instead (D-019). The pairing is
self-consistent — file-based storage is single-operator only (A4), so it only
ever runs one worker, which is exactly where this adapter belongs.

Not durable: pair with :class:`weave_core.events.ingress.IngressLog`
(append-then-publish) for at-least-once, replayable ingress.
"""

from __future__ import annotations

from typing import Dict, List

from weave_core.utils import logger

from weave_core.events.bus import Handler, WILDCARD
from weave_core.events.schema import Event


class InProcessBus:
    """Single-process async pub/sub. Exact-type handlers first, then wildcards."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Handler]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subs.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        handlers = list(self._subs.get(event.type, [])) + list(
            self._subs.get(WILDCARD, [])
        )
        if not handlers:
            logger.debug(f"InProcessBus: no subscribers for '{event.type}'")
        for handler in handlers:
            await handler(event)
