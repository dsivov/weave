"""What the extractor is told to look for — read from the ontology, per run (P15, D-050).

Weave installs an ontology into a workspace as **signed governance**: the object
types the whole answer surface is built on. Extraction never read it. It used
`DEFAULT_ENTITY_TYPES`, a hand-kept list of fourteen carried from the parent
engine, and the two vocabularies **do not overlap at all**:

    extracted        artifact · concept · method · data · objection ·
                     constraint · person · content · UNKNOWN
    answered         Feature · ChangeRequest · Task · ArchitectureDecisionRecord ·
                     Review · Insight · PRD · RFC · Commit · Module …

So `/ask/features` seeded on `Feature` and found only the handful a human had
created by hand — out of 975 nodes. **The answer surface was not thin because
the data was thin; it was looking for types the pipeline could not produce.*

`Objection`, `Competitor` and `LossReason` in that list are the same sales
vocabulary as the AudioRival example D-041 removed. P11 replaced the
illustrations; this is the schema they illustrated.

## The chain

    explicit WEAVE_ENTITY_TYPES  →  the workspace's installed ontology  →  the shipped preset

An explicit override still wins, because an operator who sets it means it. The
shipped preset is the floor rather than the parent's list, so **a workspace that
ingests before `bootstrap` still produces Weave types** — the case where "read
the installed ontology" alone would silently fall back to nothing.

## Read per run, never captured

**This is the part that must not be got wrong** (A8). The ontology is signed and
versioned and changes *without a restart*. Types captured when the engine is
constructed go stale the moment somebody signs a new version — and every test
you would think to write still passes, because a test builds an engine and
extracts in the same breath. That is the wizard writing what the runtime does not
read, arriving from a third direction, and A8 exists to prevent exactly it.

So this module exposes a **resolver**: a callable the extraction path invokes
each time it runs, not a list anybody stores.
"""

from __future__ import annotations

import os
from typing import Any, Callable, List, Optional

#: What the shipped preset's ontology defines, loaded lazily so importing this
#: module costs nothing and a preset change cannot be captured either.
def preset_entity_types() -> List[str]:
    """The floor of the chain: the ontology Weave ships.

    Used when a workspace has no installed ontology yet — a graph being written
    before anybody ran `bootstrap`. Falling through to *nothing* there would
    reproduce the defect for every new workspace's first ingest, which is the
    ingest that matters most: it is the one that reads the repository.
    """
    try:
        from weave.team import preset

        ontology = preset.load_part("ontology") or {}
        return [o["name"] for o in ontology.get("object_types", []) if o.get("name")]
    except Exception:  # pragma: no cover - a broken preset is its own failure
        return []


def explicit_entity_types(env: Optional[dict] = None) -> List[str]:
    """`WEAVE_ENTITY_TYPES`, when an operator has set it.

    Kept at the head of the chain because an override that the installed
    ontology could silently outvote is not an override.

    **Both spellings are accepted, and that is not politeness.** This variable
    was read in two places with two parsers: `get_env_value(..., list)` wanted
    JSON and warned-then-ignored anything else, while the resolver split on
    commas. So `WEAVE_ENTITY_TYPES=PRD,RFC` silently did nothing on one path and
    worked on the other — and D-050's rollback instruction, *"set
    WEAVE_ENTITY_TYPES to the old list"*, would have been a trap either way.
    One parser now, and it takes what an operator would plausibly write; a
    setting that silently means nothing is worse than one that refuses.
    """
    raw = str((env if env is not None else os.environ).get("WEAVE_ENTITY_TYPES", "")).strip()
    if not raw:
        return []
    if raw.startswith("["):
        import json

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except ValueError:
            pass   # fall through and treat it as a list of names
    return [part.strip().strip('"\'') for part in raw.strip("[]").split(",")
            if part.strip().strip('"\'')]


def make_resolver(ontology_service: Any = None) -> Callable[[str], List[str]]:
    """A callable the extraction path invokes **at extraction time**.

    Takes the workspace, because one pool serves many and the installed ontology
    is per-workspace. Returns the first non-empty step of the chain.
    """

    def resolve(workspace: str = "") -> List[str]:
        explicit = explicit_entity_types()
        if explicit:
            return explicit

        if ontology_service is not None:
            try:
                summary = ontology_service.get_summary(workspace or "default")
                if summary.get("exists"):
                    installed = [o["name"] for o in summary.get("object_types", [])
                                 if o.get("name")]
                    if installed:
                        return installed
            except Exception:  # pragma: no cover - never fail an ingest on this
                pass

        return preset_entity_types()

    return resolve
