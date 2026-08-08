"""Node-quality filtering (Graph-Quality v-next, Phase 2 / Topic 2).

A deterministic, conservative gate that keeps the obvious garbage out of the graph
without needing an ontology (D12: conservative strictness). Pronoun/deictic/stop-word
names and empty descriptions are rejected; everything else passes. Failing nodes are
quarantined (not dropped) by the caller.
"""

from weave_core.knowledge.quality.gate import (
    QualityVerdict,
    quality_check,
    is_garbage_name,
)
from weave_core.knowledge.quality.filter import (
    NodeFilter,
    FilterResult,
    ontology_from_types,
)
from weave_core.knowledge.quality.store import (
    QuarantineStore,
    InMemoryQuarantineStore,
    JsonQuarantineStore,
)

__all__ = [
    "QualityVerdict",
    "quality_check",
    "is_garbage_name",
    "NodeFilter",
    "FilterResult",
    "ontology_from_types",
    "QuarantineStore",
    "InMemoryQuarantineStore",
    "JsonQuarantineStore",
]
