"""Weave diagrams — pictures as governed artifacts (P6).

A diagram is mermaid source that versions, signs, and reverts through the same
Studio gesture as a rule or an ontology, and links to what it depicts. The
:class:`DiagramAuthor` drafts one from a natural-language description; the
:class:`DiagramStore` keeps the versioned artifacts. See docs/WEAVE_RFC.html (P6).
"""

from weave_core.studio.diagrams.agent import (
    DiagramAuthor,
    DiagramGenerationResult,
    structural_problems,
)
from weave_core.studio.diagrams.schema import (
    DIAGRAM_TYPES,
    Diagram,
    signature,
    unsafe_content,
)
from weave_core.studio.diagrams.store import (
    DiagramStore,
    InMemoryDiagramStore,
    JsonDiagramStore,
)

__all__ = [
    "DIAGRAM_TYPES",
    "Diagram",
    "DiagramAuthor",
    "DiagramGenerationResult",
    "DiagramStore",
    "InMemoryDiagramStore",
    "JsonDiagramStore",
    "signature",
    "structural_problems",
    "unsafe_content",
]
