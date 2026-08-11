"""`Locator` — how an artifact node points at the document it is about (R21).

An artifact that copies its source rots against the repository: the file moves,
the paragraph is rewritten, and the node goes on asserting something that stopped
being true, with nothing to detect it. So an artifact node stores a *pointer* —
repository, path, revision, and optionally an anchor within the file — and never
a copy of the body (A5).

**The revision is the load-bearing field.** A locator resolves against the `rev`
it recorded, never against a moving `HEAD` (R23): a file that was reorganised
last week still resolves for the review that cited it, because the review cited
a commit. Resolution against `HEAD` would make every reorganisation silently
invalidate history, which is the failure this design exists to avoid.

**Flat on the node, structured in code.** The ontology's property model has no
nested kind, so a locator lands on a node as four `locator_*` properties. That
projection lives here rather than in each caller, so there is one spelling of
the field names — the same reasoning that makes `WORKSPACE_HEADER` a constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

#: Prefix for the flat node properties. The ontology declares
#: `locator_repo`, `locator_path`, `locator_rev` and `locator_anchor` on every
#: artifact type; see `weave/team/preset/ontology.json`.
PROPERTY_PREFIX = "locator_"

#: The fields, in the order a human reads them.
FIELDS = ("repo", "path", "rev", "anchor")

#: The node property names, derived so the two can never drift apart.
PROPERTY_NAMES = tuple(f"{PROPERTY_PREFIX}{name}" for name in FIELDS)


class LocatorError(ValueError):
    """A locator that cannot identify a document."""


@dataclass(frozen=True)
class Locator:
    """A pointer to a document at a known revision.

    Frozen because a locator is a citation: the whole point is that it means the
    same thing tomorrow. Mutating one in place would silently rewrite what an
    existing artifact claims to be about.
    """

    repo: str
    path: str
    rev: str
    anchor: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("repo", "path", "rev"):
            if not (getattr(self, field_name) or "").strip():
                raise LocatorError(
                    f"a locator needs a {field_name}; got {self.to_dict()!r}. "
                    "A locator without a rev resolves against a moving HEAD, "
                    "which is the rot this field exists to prevent (R23)."
                )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d = {"repo": self.repo, "path": self.path, "rev": self.rev}
        if self.anchor:
            d["anchor"] = self.anchor
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Locator":
        return cls(
            repo=str(d.get("repo") or ""),
            path=str(d.get("path") or ""),
            rev=str(d.get("rev") or ""),
            anchor=(str(d["anchor"]) if d.get("anchor") else None),
        )

    # -- the flat projection an ontology node carries -----------------------

    def to_node_properties(self) -> Dict[str, str]:
        """The `locator_*` properties to merge onto a graph node.

        The anchor is omitted when absent rather than written empty, so a node
        that never had one is distinguishable from one whose anchor was cleared.
        """
        props = {
            f"{PROPERTY_PREFIX}repo": self.repo,
            f"{PROPERTY_PREFIX}path": self.path,
            f"{PROPERTY_PREFIX}rev": self.rev,
        }
        if self.anchor:
            props[f"{PROPERTY_PREFIX}anchor"] = self.anchor
        return props

    @classmethod
    def from_node_properties(cls, props: Mapping[str, Any]) -> Optional["Locator"]:
        """Read a locator off a node, or None if the node does not carry one.

        Returns None rather than raising for a node with no locator at all,
        because most nodes in a graph legitimately have none — a `Role` is not an
        artifact. A node carrying a *partial* locator is a different matter and
        does raise: it is a bug that would otherwise resolve against the wrong
        revision or fail far from its cause.
        """
        present = {
            name: str(props[f"{PROPERTY_PREFIX}{name}"])
            for name in FIELDS
            if props.get(f"{PROPERTY_PREFIX}{name}")
        }
        if not present:
            return None
        missing = {"repo", "path", "rev"} - present.keys()
        if missing:
            raise LocatorError(
                f"node carries a partial locator, missing {sorted(missing)}: "
                f"{present!r}"
            )
        return cls(**present)


def sha_of(props: Mapping[str, Any]) -> Optional[str]:
    """The `sha` a `Commit` node carries (R21), or None.

    A `Commit` records `sha` as its own property as well as inside its locator's
    `rev`: the locator says *where to look*, the sha says *which commit this node
    is*. They are usually equal and the resolver check does not require them to
    be — a commit node may cite a path in a different repository.
    """
    sha = props.get("sha")
    return str(sha) if sha else None
