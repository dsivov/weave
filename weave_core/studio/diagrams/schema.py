"""Diagram artifact + its structural signature (P6).

A :class:`Diagram` is a **governed, versioned artifact**: mermaid source plus the
metadata that ties it to the things it depicts (the ``depicts`` link in the Weave
ontology: ``Diagram -> ChangeRequest | Module``). It authors, versions, signs, and
reverts through exactly the same Studio gesture as an ontology, rule, flow, or
action — propose → assess → apply — so a picture of the system carries the same
audit trail as the system's policy.

**What counts as a behaviour change.** A diagram does not execute, so "behaviour"
here is its *structure*: which nodes exist and how they connect. :func:`signature`
reduces mermaid source to that skeleton, discarding what only affects how the
picture reads — display labels, styling (``style``/``classDef``/``linkStyle``),
click handlers, layout direction, titles, and comments. Redrawing an arrow is a
behavioural change needing full sign-off; rewording a box label is cosmetic and
takes the lightweight path. This mirrors the engine's treatment of a renamed rule
or a reworded reason (docs/PLATFORM_ARCHITECTURE.html, decisions 4/5/7).

See docs/WEAVE_RFC.html (P6) and docs/PLATFORM_WORK_PLAN.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

# Recognized mermaid diagram headers. Open-world in spirit but closed here on
# purpose: the header is how we reject a blob of prose that is not a diagram at
# all before it ever reaches the ledger.
DIAGRAM_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2",
    "stateDiagram", "erDiagram", "journey", "gantt", "pie", "quadrantChart",
    "requirementDiagram", "gitGraph", "mindmap", "timeline", "sankey-beta",
    "xychart-beta", "block-beta", "packet-beta", "kanban", "architecture-beta",
    "C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment",
)

# Statement prefixes that change how a diagram *looks*, never what it depicts.
_COSMETIC_PREFIXES = (
    "style", "classdef", "linkstyle", "click", "direction", "title",
    "acctitle", "accdescr", "autonumber",
)

_COMMENT_RE = re.compile(r"%%.*$", re.MULTILINE)
_INIT_DIRECTIVE_RE = re.compile(r"%%\{.*?\}%%", re.DOTALL)

# Node display labels: `A[text]`, `A(text)`, `A{text}`, `A>text]` and the
# compound shapes. Longest forms first so `[[x]]` is not eaten as `[x]`.
_NODE_LABEL_RE = re.compile(
    r"(?<=[A-Za-z0-9_])"
    r"(\[\[.*?\]\]|\[\(.*?\)\]|\(\(\(.*?\)\)\)|\(\(.*?\)\)|\(\[.*?\]\)"
    r"|\{\{.*?\}\}|\[/.*?[/\\]\]|\[\\.*?[/\\]\]|\[.*?\]|\(.*?\)|\{.*?\}|>.*?\])"
)
_PIPE_LABEL_RE = re.compile(r"\|[^|]*\|")
# Characters that make up ER cardinality (`||--o{`, `}o..o|`). A pipe group built
# only from these is part of the connector, not a label — stripping it would make
# a cardinality change look cosmetic.
_LINK_CHARS = set("o{}.-=<> ")

# A connector: a run of link characters with at least two "line" characters
# (`--`, `==`, `-.`) or a line character followed by an arrowhead (`->`, `->>`).
# Covers flowchart (`-->`, `-.->`, `==>`), sequence (`->>`, `--)`), class
# (`<|--`, `*--`), and ER cardinality (`||--o{`, `}o..o|`).
_ARROW_RE = re.compile(r"[<>ox|{}*+]*[-.=~]{2,}[<>ox|{}*+]*|[<>ox|{}*+]*[-.=~][<>ox|{}*+]+")
# An arrowhead-bearing connector terminates an edge; a bare `--` means a label follows.
_HEAD_RE = re.compile(r"[<>ox|{}*+]")


@dataclass
class Diagram:
    """One versioned mermaid diagram.

    ``depicts`` holds the ids/names this diagram is about (a change request, a
    module, a Weave task) so the graph can answer "what pictures cover this?".
    """

    id: str
    title: str = ""
    source: str = ""                                   # mermaid text
    description: str = ""
    depicts: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    version: int = 1

    # -- derived ------------------------------------------------------------

    def diagram_type(self) -> str:
        """The mermaid header keyword, or ``""`` if the source has none."""
        for line in _statements(self.source):
            for t in DIAGRAM_TYPES:
                if line == t or line.startswith(t + " ") or line.startswith(t + "\n"):
                    return t
        return ""

    def signature(self) -> Tuple[str, FrozenSet[str]]:
        """The structural skeleton: ``(type, {canonical statements})``.

        Equal signatures mean the diagram depicts the same thing; the difference
        is cosmetic.
        """
        return signature(self.source)

    def lint(self) -> List[str]:
        problems: List[str] = []
        if not self.id:
            problems.append("id is required")
        if not (self.source or "").strip():
            problems.append("source is required")
        elif not self.diagram_type():
            problems.append(
                "source does not start with a known mermaid diagram type "
                f"(expected one of: {', '.join(DIAGRAM_TYPES[:6])}, …)"
            )
        problems.extend(unsafe_content(self.source))
        return problems

    # -- (de)serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "description": self.description,
            "depicts": list(self.depicts),
            "tags": list(self.tags),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Diagram":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            source=d.get("source", "") or "",
            description=d.get("description", ""),
            depicts=list(d.get("depicts") or []),
            tags=list(d.get("tags") or []),
            version=int(d.get("version", 1)),
        )


# ── structural signature ────────────────────────────────────────────────────


def signature(source: Optional[str]) -> Tuple[str, FrozenSet[str]]:
    """Reduce mermaid source to what it depicts, dropping presentation.

    Node ids and connectors survive; labels, styling, click handlers, layout
    direction, titles, and comments do not.
    """
    kind = ""
    statements: set[str] = set()
    for raw in _statements(source):
        low = raw.lower()
        if not kind:
            match = next((t for t in DIAGRAM_TYPES
                          if low == t.lower() or low.startswith(t.lower() + " ")), None)
            if match:
                kind = match
                continue
        if any(low == p or low.startswith(p + " ") or low.startswith(p + ":")
               for p in _COSMETIC_PREFIXES):
            continue
        # `class A,B styleName` styles flowchart nodes; in a classDiagram the
        # same keyword declares a real type, so only drop it for graph kinds.
        if low.startswith("class ") and kind in ("flowchart", "graph"):
            continue
        canonical = _canonical(raw)
        if canonical:
            statements.add(canonical)
    return kind, frozenset(_drop_redundant_declarations(statements))


def _drop_redundant_declarations(statements: Set[str]) -> Set[str]:
    """Forget a bare node declaration when an edge already introduces that node.

    ``A[Plan] --> B{Ready}`` and the equivalent written as three lines — two
    declarations plus ``A --> B`` — draw the same picture, and a visual editor
    round-trips the first form into the second. Counting the declarations as
    structure of their own would make opening a diagram and saving it back
    unchanged look like a redraw, so every save would demand a sign-off and the
    gate would stop meaning anything.

    A declaration still survives when nothing connects to it: an isolated node
    is genuinely part of what the diagram depicts, and adding or removing one is
    a structural change.
    """
    connected: Set[str] = set()
    for s in statements:
        arrows = list(_ARROW_RE.finditer(s))
        if not arrows:
            continue
        prev = 0
        for a in arrows:
            connected.add(s[prev:a.start()].strip())
            prev = a.end()
        connected.add(s[prev:].strip())
    return {s for s in statements
            if _ARROW_RE.search(s) or s not in connected}


def _statements(source: Optional[str]) -> List[str]:
    """Comment-free, whitespace-normalized, non-empty lines."""
    text = _INIT_DIRECTIVE_RE.sub("", source or "")
    text = _COMMENT_RE.sub("", text)
    out: List[str] = []
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip().rstrip(";")
        if s:
            out.append(s)
    return out


def _canonical(statement: str) -> str:
    """One statement reduced to `src<arrow>dst` (edges) or a label-free
    declaration (nodes, participants, subgraphs)."""
    s = _strip_pipe_labels(statement)               # edge labels: A -->|yes| B
    s = _NODE_LABEL_RE.sub("", s)                   # node labels: A[Manager]

    arrows = list(_ARROW_RE.finditer(s))
    if not arrows:
        return re.sub(r"\s+", " ", s).strip()

    # `A -- yes --> B`: a headless connector means the next segment is a label,
    # so the edge runs to what follows the *last* connector. `A --> B --> C`
    # chains instead, since each connector carries a head.
    parts: List[str] = []
    prev_end = 0
    for m in arrows:
        parts.append(s[prev_end:m.start()])
        prev_end = m.end()
    parts.append(s[prev_end:])

    edges: List[str] = []
    src = _term(parts[0])
    for i, m in enumerate(arrows):
        arrow = m.group(0)
        nxt = _term(parts[i + 1])
        if not _HEAD_RE.search(arrow) and i + 1 < len(arrows):
            continue                                # headless: `nxt` was a label
        head_arrow = arrow if _HEAD_RE.search(arrow) else arrows[-1].group(0)
        if src and nxt:
            edges.append(f"{src}{head_arrow}{nxt}")
        src = nxt
    return " ".join(edges) if edges else re.sub(r"\s+", " ", s).strip()


def _strip_pipe_labels(statement: str) -> str:
    """Drop `|text|` edge labels but keep `||`/`|o` ER cardinality."""
    def repl(m: re.Match) -> str:
        inner = m.group(0)[1:-1]
        return " " if inner and any(c not in _LINK_CHARS for c in inner) else m.group(0)

    return _PIPE_LABEL_RE.sub(repl, statement)


def _term(fragment: str) -> str:
    """The identifier at an edge end — the last token of the left side / first
    token of the right side, stripped of quotes and trailing message text."""
    frag = fragment.split(":")[0]                    # `Alice->>Bob: hi` message
    tokens = [t.strip("\"'`") for t in frag.split() if t.strip("\"'`")]
    return tokens[-1] if tokens else ""


# ── safety ──────────────────────────────────────────────────────────────────

_UNSAFE_PATTERNS = (
    (re.compile(r"<\s*script", re.IGNORECASE), "embedded <script>"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "javascript: URL"),
    (re.compile(r"\bon(?:error|load|click)\s*=", re.IGNORECASE), "inline event handler"),
)


def unsafe_content(source: Optional[str]) -> List[str]:
    """Content a signed diagram must never carry.

    The WebUI renders mermaid with ``securityLevel: 'loose'`` (needed for the
    click/HTML label features), so scripts and ``javascript:`` targets are
    rejected at the authoring gate rather than trusted at render time.
    """
    text = source or ""
    return [f"source contains {label}" for pattern, label in _UNSAFE_PATTERNS
            if pattern.search(text)]
