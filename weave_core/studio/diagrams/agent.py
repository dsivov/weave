"""NL → mermaid diagram author (P6, AI-assisted authoring).

Turns a natural-language description ("show how a task flows from the Architect
to a developer worker and back through review") into a validated
:class:`~weave_core.studio.diagrams.schema.Diagram`. Mirrors ``RuleAuthor`` and
``OntologyAuthor``: the LLM is *constrained, not trusted*. Every draft must

* carry a recognized mermaid diagram header,
* pass ``Diagram.lint()`` (no script/``javascript:`` content reaches the ledger),
* be structurally sane (balanced delimiters, more than just a header), and
* reduce to a non-empty structural signature — a picture with no nodes or edges
  is prose, not a diagram.

Failed drafts are fed back for a bounded number of auto-repair rounds.

The LLM is the workspace's ``llm_model_func`` (``await llm(prompt,
system_prompt=...) -> str``); nothing here is provider-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from weave_core.jsonio import _extract_json_object
from weave_core.utils import logger

from weave_core.studio.diagrams.schema import DIAGRAM_TYPES, Diagram, signature

LLMFunc = Callable[..., Awaitable[str]]

_TYPE_HELP = {
    "flowchart": "processes, architectures, decision paths (the default choice)",
    "sequenceDiagram": "an ordered exchange between participants over time",
    "stateDiagram-v2": "a lifecycle: states and the transitions between them",
    "erDiagram": "data entities and their relationships",
    "classDiagram": "types, their fields, and inheritance/composition",
    "gantt": "scheduled work over calendar time",
    "C4Context": "system context — actors and external systems",
}


@dataclass
class DiagramGenerationResult:
    """The outcome of an NL→diagram generation attempt."""

    valid: bool
    diagram: Dict[str, Any] = field(default_factory=dict)     # Diagram.to_dict()
    lint: List[str] = field(default_factory=list)
    explanation: str = ""
    errors: List[str] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "diagram": self.diagram,
            "lint": self.lint,
            "explanation": self.explanation,
            "errors": self.errors,
            "attempts": self.attempts,
        }


class DiagramAuthor:
    """Drafts, validates, and repairs a mermaid diagram from a description."""

    def __init__(self, llm: LLMFunc) -> None:
        self._llm = llm

    # -- prompts -------------------------------------------------------------

    def _system_prompt(self) -> str:
        types = "\n".join(f"  - {t}: {d}" for t, d in _TYPE_HELP.items())
        return f"""You convert a natural-language description of a system, process, \
or lifecycle into a MERMAID diagram. Output STRICT JSON only.

Pick the diagram type that fits the description:
{types}
(Any mermaid type is allowed; these are the common ones.)

Rules:
- "source" MUST be valid mermaid, starting with the diagram-type keyword \
(e.g. `flowchart LR`), with real newlines between statements.
- Give every node a short stable id and a human label: `arch[Architect]`.
- Label the edges that need it: `arch -->|publishes plan| queue`.
- Model what the description actually says — do not invent components it never \
mentions, and do not omit ones it does.
- Keep it readable: prefer 5-15 nodes. Use `subgraph` to group when it clarifies.
- NEVER include <script>, `javascript:` URLs, inline event handlers, or `click` \
directives — the diagram is a signed artifact and these are rejected.

Return JSON with exactly these keys:
{{
  "diagram": {{
    "title": "<short human title>",
    "source": "flowchart LR\\n  a[Architect] -->|publishes plan| q[Task queue]\\n  q --> d[Developer]",
    "description": "<one sentence on what the diagram shows>",
    "depicts": ["<change request / module / task id this is about>"],
    "tags": ["<optional topic tags>"]
  }},
  "explanation": "<one sentence describing what you drew and why that type>"
}}"""

    def _user_prompt(self, description: str, current: Optional[Dict[str, Any]],
                     repair: Optional[str]) -> str:
        parts = [f"Diagram to draw:\n{description.strip()}"]
        if current and current.get("source"):
            parts.append(
                "Revise this existing diagram — keep what still applies, change only "
                f"what the request implies:\n```mermaid\n{current['source']}\n```"
            )
        parts.append("Produce the JSON.")
        if repair:
            parts.append(
                f"YOUR PREVIOUS ATTEMPT WAS INVALID:\n{repair}\nFix it and return corrected JSON."
            )
        return "\n\n".join(parts)

    # -- generation loop -----------------------------------------------------

    async def generate(
        self,
        description: str,
        *,
        diagram_id: str = "diagram",
        current: Optional[Dict[str, Any]] = None,
        max_repairs: int = 1,
    ) -> DiagramGenerationResult:
        errors: List[str] = []
        last: Dict[str, Any] = {}

        for attempt in range(max_repairs + 1):
            raw = await self._llm(
                self._user_prompt(description, current, errors[-1] if errors else None),
                system_prompt=self._system_prompt(),
            )
            payload = _extract_json_object(raw)
            if payload is None:
                errors.append("the model did not return a JSON object")
                continue

            d = payload.get("diagram")
            if not isinstance(d, dict):
                errors.append("missing 'diagram' object")
                continue

            diagram = Diagram.from_dict({**d, "id": diagram_id})
            # Preserve the caller's linkage when the model drops it.
            if not diagram.depicts and current:
                diagram.depicts = list(current.get("depicts") or [])
            last = diagram.to_dict()

            problems = diagram.lint() + structural_problems(diagram.source)
            if problems:
                errors.append("; ".join(problems))
                continue

            logger.info(
                f"DiagramAuthor produced a valid {diagram.diagram_type()} after "
                f"{attempt + 1} attempt(s)"
            )
            return DiagramGenerationResult(
                valid=True, diagram=diagram.to_dict(), lint=[],
                explanation=payload.get("explanation", ""),
                errors=errors, attempts=attempt + 1,
            )

        return DiagramGenerationResult(
            valid=False, diagram=last, lint=list(errors),
            errors=errors, attempts=max_repairs + 1,
        )


def structural_problems(source: Optional[str]) -> List[str]:
    """Cheap offline sanity checks that catch the ways an LLM breaks mermaid,
    without needing a renderer."""
    problems: List[str] = []
    text = source or ""
    kind, statements = signature(text)
    if kind and not statements:
        problems.append(f"the {kind} has no nodes or edges — it is a header, not a diagram")
    for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
        if text.count(opener) != text.count(closer):
            problems.append(f"unbalanced '{opener}' / '{closer}' in the source")
    if text and not kind:
        problems.append(
            "source does not start with a known mermaid diagram type "
            f"(expected one of: {', '.join(DIAGRAM_TYPES[:6])}, …)"
        )
    return problems
