"""The ``EventBus`` port — one interface, one adapter per deployment shape (A7).

This module holds the *port* only: the handler type, the wildcard token, and the
protocol an adapter must satisfy. It deliberately contains no implementation, so
which bus a deployment runs is a configuration choice rather than an import.

Two adapters exist, and **the adapter must match the deployment** (A7, D-019):

* :class:`weave_core.events.inprocess.InProcessBus` — single-process only.
* the PostgreSQL ``LISTEN/NOTIFY`` adapter — any multi-worker deployment.

The failure this pairing prevents is silent: put SSE on the in-process bus behind
gunicorn ``--workers 2`` and a client connected to worker 2 never receives an
event published on worker 1. Nothing errors, nothing logs, the board just stops
updating for some users.

Neither adapter is durable. Pair the bus with
:class:`weave_core.events.ingress.IngressLog` (append-then-publish) when you need
at-least-once, replayable delivery.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from weave_core.events.schema import Event

#: An event handler: an async callable taking one :class:`Event`.
Handler = Callable[[Event], Awaitable[None]]

#: Subscribe with this event type to receive every event.
WILDCARD = "*"


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe, narrow on purpose — two methods is the whole contract."""

    def subscribe(self, event_type: str, handler: Handler) -> None: ...

    async def publish(self, event: Event) -> None: ...
