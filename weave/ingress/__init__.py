"""Weave integration engine — events in, typed and deduped (P1).

    from weave.ingress import IngressService, MappingSpec
    from weave_core.events import InProcessBus
    from weave_core.events.ingress import JsonIngressLog

    svc = IngressService(JsonIngressLog("./rag_storage/ingress"), InProcessBus())
    svc.set_mapping("acme", "webhook", MappingSpec(event_type="discount.requested",
                                                   fields={"customer": "customer"}))
    result = await svc.receive("acme", "webhook", payload)

See docs/PLATFORM_ARCHITECTURE.html (integration engine; decisions 2/3).
"""

from weave.ingress.connectors import (
    DEFAULT_CONNECTORS,
    IngressConnector,
    WebhookConnector,
)
from weave.ingress.mapper import DeterministicMapper, Mapper
from weave.ingress.schema import (
    DecisionBinding,
    MappingError,
    MappingSpec,
    RawRecord,
    pluck,
)
from weave.ingress.service import (
    DecisionSubscriber,
    IngressResult,
    IngressService,
)

__all__ = [
    "DEFAULT_CONNECTORS",
    "IngressConnector",
    "WebhookConnector",
    "Mapper",
    "DeterministicMapper",
    "DecisionBinding",
    "MappingError",
    "MappingSpec",
    "RawRecord",
    "pluck",
    "DecisionSubscriber",
    "IngressResult",
    "IngressService",
]
