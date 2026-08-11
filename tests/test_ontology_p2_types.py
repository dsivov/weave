"""P2's data model: the four new object types, the locator, and the link types.

R19 asks for `Feature`, `Review`, `Insight` and `Question` as object types, with
`implemented_by`, `specified_by`, `depicted_by` and `answered_by` as link types.
R21 puts a locator on every artifact node and a `sha` on `Commit`. The M2 gate
adds one more: `reviewed_in` must terminate on a `Review` node, and no declared
link type may point at nothing.

Two of those link types were **retargeted** rather than added, which is the part
worth being careful about:

- `implemented_by` was Task→Commit and is now Feature→Task, which is what R19
  and the DRP class diagram mean by it; the task-to-commit leg is `produced`.
- `reviewed_in` was PullRequest→ArchitectureDecisionRecord and now terminates on
  `Review`. The ADR leg is what `justified_by` is for, so nothing is lost.

Retargeting is safe here for a specific reason that will not be obvious later:
**link-type names are declarations, not stored edge labels.** Graph edges carry
a prose relation string (`coordinator.record_commit` writes "implemented by a
commit"), and `ontology_service.save()` replaces the whole document, so there is
no data to migrate and nothing reads the names at runtime. Elsewhere that may
not hold — hence the assertion below that it still holds here.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from weave.team import preset
from weave_core.governance.ontology import Ontology

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: A5: these carry a reference to their source, never a copy of its body.
ARTIFACT_TYPES = [
    "PRD", "RFC", "ArchitectureDecisionRecord", "Diagram", "ChangeRequest",
    "Task", "Feature", "Review", "Insight",
]

LOCATOR_PROPERTIES = ["locator_repo", "locator_path", "locator_rev", "locator_anchor"]


@pytest.fixture(scope="module")
def ontology() -> dict:
    return preset.load_part("ontology")


@pytest.fixture(scope="module")
def object_types(ontology) -> dict:
    return {o["name"]: o for o in ontology["object_types"]}


@pytest.fixture(scope="module")
def link_types(ontology) -> dict:
    return {link["name"]: link for link in ontology["link_types"]}


def _property_names(object_type: dict) -> set:
    return {p["name"] for p in object_type["properties"]}


# ── R19 · the four object types ──────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("name", ["Feature", "Review", "Insight", "Question"])
def test_the_new_object_types_exist(object_types, name):
    assert name in object_types, f"R19 requires a {name} object type"
    assert object_types[name]["description"].strip(), "an undescribed type teaches nothing"


@pytest.mark.offline
def test_the_new_types_carry_the_fields_the_drp_declares(object_types):
    assert {"title", "status", "summary"} <= _property_names(object_types["Feature"])
    assert {"verdict", "summary", "reviewer"} <= _property_names(object_types["Review"])
    assert {"statement", "confidence"} <= _property_names(object_types["Insight"])
    assert {"text", "asked_by", "asked_at"} <= _property_names(object_types["Question"])


@pytest.mark.offline
def test_a_question_records_who_asked_and_when(object_types):
    """R27: a repeat question should surface the prior answer, which needs the
    asker and the time recorded rather than inferred."""
    props = {p["name"]: p for p in object_types["Question"]["properties"]}
    assert props["asked_at"]["kind"] == "date"
    assert props["text"].get("required") is True


# ── R21 · the locator, and no embedded bodies (A5) ───────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("name", ARTIFACT_TYPES)
def test_every_artifact_type_carries_a_locator(object_types, name):
    """A5: an artifact references its source by repo · path · rev. An artifact
    that copies the body instead rots against the repository silently."""
    missing = set(LOCATOR_PROPERTIES) - _property_names(object_types[name])
    assert not missing, f"{name} is an artifact node and is missing {sorted(missing)}"


@pytest.mark.offline
def test_commit_carries_a_resolving_sha(object_types):
    """R21 and the M2 gate: `Commit` additionally carries `sha`. Before P2 it had
    only a subject line, which cannot be resolved to anything."""
    assert "sha" in _property_names(object_types["Commit"])


@pytest.mark.offline
@pytest.mark.parametrize("name", ARTIFACT_TYPES)
def test_no_artifact_type_has_a_field_that_would_hold_a_document_body(
    object_types, name
):
    """A5 is about storage, so it is asserted against the vocabulary that would
    invite it. `summary` and `rationale` are deliberate exceptions: they are
    abstracts written *about* the source, and both predate P2."""
    body_fields = {"body", "content", "text_content", "document", "full_text", "source"}
    offending = body_fields & _property_names(object_types[name])
    assert not offending, (
        f"{name} declares {sorted(offending)} — artifact nodes reference their "
        "source by locator and never embed a copy of it (A5)"
    )


# ── R19 · the link types, including the two retargeted ones ──────────────────


@pytest.mark.offline
@pytest.mark.parametrize(
    "name", ["implemented_by", "specified_by", "depicted_by", "answered_by"]
)
def test_the_r19_link_types_exist(link_types, name):
    assert name in link_types


@pytest.mark.offline
def test_implemented_by_runs_feature_to_task(link_types):
    """Retargeted from Task→Commit, which is now `produced`."""
    link = link_types["implemented_by"]
    assert link["source_types"] == ["Feature"]
    assert link["target_types"] == ["Task"]
    assert link_types["produced"]["source_types"] == ["Task"]
    assert link_types["produced"]["target_types"] == ["Commit"]


@pytest.mark.offline
def test_reviewed_in_terminates_on_a_review_node(link_types):
    """An M2 gate criterion, stated verbatim."""
    link = link_types["reviewed_in"]
    assert link["target_types"] == ["Review"], (
        "reviewed_in must terminate on a Review node; it pointed at an "
        "ArchitectureDecisionRecord before P2, which is what justified_by is for"
    )
    assert "Task" in link["source_types"]


@pytest.mark.offline
def test_learnings_can_be_traversed_from_a_review(link_types):
    """`/ask/learnings` walks Review→Insight, so the edge has to be declared."""
    assert link_types["yielded"]["source_types"] == ["Review"]
    assert link_types["yielded"]["target_types"] == ["Insight"]


@pytest.mark.offline
def test_a_question_can_reach_what_answered_it(link_types):
    link = link_types["answered_by"]
    assert link["source_types"] == ["Question"]
    assert "Insight" in link["target_types"]


# ── the M2 gate: no declared link type points at nothing ─────────────────────


@pytest.mark.offline
def test_no_declared_link_type_points_at_an_undefined_object_type(
    ontology, object_types
):
    """The gate criterion. Also what `OntologyService.save()` enforces — asserted
    here as well so a broken preset fails at build time with a message that names
    the link rather than at install time with a validation error."""
    declared = set(object_types)
    dangling = []
    for link in ontology["link_types"]:
        for side in ("source_types", "target_types"):
            for type_name in link.get(side, []):
                if type_name not in declared:
                    dangling.append(f"{link['name']}.{side} → {type_name}")
    assert not dangling, "link types referencing undefined object types: " + str(dangling)


@pytest.mark.offline
def test_the_preset_still_validates_and_loads(ontology):
    """The whole document must parse through the schema the service enforces —
    the retargeting must not have produced something that only looks right."""
    assert preset.validate() == []
    loaded = Ontology.from_dict(ontology)
    assert loaded.name == "weave"
    assert len(loaded.object_types) == len(ontology["object_types"])
    assert len(loaded.link_types) == len(ontology["link_types"])


# ── `depicted_by` is a declared view, never a stored edge ────────────────────


@pytest.mark.offline
def test_depicted_by_has_no_write_side(link_types):
    """`depicts` and `depicted_by` are two directions of one relationship, not
    two mechanisms for one job (R10).

    What would make it a violation is a second **write** side, because then the
    two could disagree with nothing to reconcile them. There is exactly one:
    `Diagram.depicts`, a stored field the Studio owns. The reverse direction is
    derived at read time by `DiagramStore.depicting()`. So the rule is that
    nothing ever writes a `depicted_by` edge — asserted here rather than left as
    a comment, because a stored inverse is precisely the kind of thing that gets
    added later for a plausible-sounding reason.
    """
    assert link_types["depicted_by"]["target_types"] == ["Diagram"]
    assert link_types["depicts"]["source_types"] == ["Diagram"]

    writers = []
    for path in sorted(_REPO.glob("weave*/**/*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and getattr(node, "body", None)
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and node.value == "depicted_by"
                and id(node) not in docstrings
            ):
                writers.append(f"{path.relative_to(_REPO)}:{node.lineno}")

    assert not writers, (
        "`depicted_by` is a declared view derived from `Diagram.depicts` at read "
        "time (DiagramStore.depicting). Nothing may write it as an edge, or the "
        "relationship gains a second write side that can disagree with the "
        "first:\n  " + "\n  ".join(writers)
    )
