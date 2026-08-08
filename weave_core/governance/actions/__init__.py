"""Weave action layer — executable operations bound to object types (P3).

Actions turn a decision graph you *reason from* into one you *operate from*: a
named, typed operation (``ApproveOrder``, ``CancelShipment``) is invoked against
an ontology object type, authorized and recorded through the business-rules gate
(``emit_decision_trace``), and — on PASS/FLAG — runs an optional side effect.
The audit edge on the graph is the record of the executed action.

See ``docs/CLOSING_THE_GAPS.html`` §08 (P3 action / kinetic layer).

    from weave_core.governance.actions import ActionCatalog, ActionDefinition, ActionParam, ActionService

    cat = ActionCatalog(name="sales").define(
        ActionDefinition("ApproveOrder", object_type="Order", relation_type="APPROVED",
                         effect="approval")
            .add(ActionParam("discount", kind="percent", required=True))
    )
"""

from weave_core.governance.actions.schema import (
    ActionParam,
    ActionHandler,
    ActionDefinition,
    ActionCatalog,
    AgentSpec,
)
from weave_core.governance.actions.store import (
    ActionStore,
    JsonActionStore,
    InMemoryActionStore,
    validate_catalog,
)
from weave_core.governance.actions.handler import (
    run_handler,
    HandlerError,
)
from weave_core.governance.actions.service import ActionService

__all__ = [
    "ActionParam",
    "ActionHandler",
    "ActionDefinition",
    "ActionCatalog",
    "AgentSpec",
    "ActionStore",
    "JsonActionStore",
    "InMemoryActionStore",
    "validate_catalog",
    "run_handler",
    "HandlerError",
    "ActionService",
]
