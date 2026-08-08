"""Weave ontology — Weave-native typed schema (P2).

The typed vocabulary the graph and the rules engine reason over: object types
with typed properties, directed link types with cardinality, and coercing
validation. See ``docs/CLOSING_THE_GAPS.html`` §07.

    from weave_core.governance.ontology import Ontology, ObjectType, LinkType, Property, PropertyKind

    onto = Ontology(name="acme").define_object(
        ObjectType("Order").add(Property("value", PropertyKind.MONEY))
    )
    onto.validate_entity("Order", {"value": "$25,000"})   # → coerced {"value": 25000.0}
"""

from weave_core.governance.ontology.schema import (
    PropertyKind,
    Cardinality,
    Property,
    ObjectType,
    LinkType,
    Ontology,
    ValidationReport,
)
from weave_core.governance.ontology.store import (
    OntologyStore,
    JsonOntologyStore,
    InMemoryOntologyStore,
    validate_ontology,
)
from weave_core.governance.ontology.validate import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionValidator,
    ExtractionReport,
    ItemValidation,
)
from weave_core.governance.ontology.agent import (
    OntologyAuthor,
    OntologyGenerationResult,
)
from weave_core.governance.ontology.service import OntologyService

__all__ = [
    "PropertyKind",
    "Cardinality",
    "Property",
    "ObjectType",
    "LinkType",
    "Ontology",
    "ValidationReport",
    "OntologyStore",
    "JsonOntologyStore",
    "InMemoryOntologyStore",
    "validate_ontology",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionValidator",
    "ExtractionReport",
    "ItemValidation",
    "OntologyAuthor",
    "OntologyGenerationResult",
    "OntologyService",
]
