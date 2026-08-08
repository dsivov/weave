"""Weave event backbone — the platform ingress + dispatch layer (P0).

    from weave_core.events import Event, InProcessBus
    from weave_core.events.ingress import JsonIngressLog

    bus = InProcessBus()
    log = JsonIngressLog("./rag_storage")
    if await log.append("acme", event):   # False if a duplicate delivery
        await bus.publish(event)

See docs/PLATFORM_ARCHITECTURE.html (decision 2 — in-process bus + durable
ingress log).
"""

from weave_core.events.schema import Event
from weave_core.events.bus import EventBus, Handler, WILDCARD
from weave_core.events.inprocess import InProcessBus
from weave_core.events.ingress import (
    IngressLog,
    InMemoryIngressLog,
    JsonIngressLog,
)

__all__ = [
    "Event",
    "EventBus",
    "Handler",
    "InProcessBus",
    "WILDCARD",
    "IngressLog",
    "InMemoryIngressLog",
    "JsonIngressLog",
]
