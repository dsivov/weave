"""Weave studio — the diff-and-approve authoring layer (P3).

Authoring (Stage 1), migration, and re-approval share one gesture: propose →
assess → (gate + sign) → apply, over a typed, versioned ArtifactDiff. The
:class:`DiffEngine` composes the ontology / rule / flow / action / diagram
services; the
:class:`StudioStore` keeps the append-only version ledger for history + revert.
See docs/PLATFORM_ARCHITECTURE.html (decisions 4/5/7).
"""

from weave_core.studio.schema import DIFF_KINDS, DIFF_ORIGINS, ArtifactDiff
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import (
    ArtifactVersion,
    InMemoryStudioStore,
    JsonStudioStore,
    SignOff,
    StudioStore,
)

__all__ = [
    "ArtifactDiff",
    "DIFF_KINDS",
    "DIFF_ORIGINS",
    "DiffEngine",
    "StudioStore",
    "InMemoryStudioStore",
    "JsonStudioStore",
    "ArtifactVersion",
    "SignOff",
]
