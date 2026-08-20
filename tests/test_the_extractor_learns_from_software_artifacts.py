"""The extraction prompt teaches on this project's artifacts (P11, D-041).

dsivov found a B2B sales conversation in the Decisions tab of the demo tenant —
*"Premium Wireless Speaker"*, *"AudioRival"*, *"Outcome: Lost (ClosedLost)"* —
and reported it as bad demo seeding. It was not demo data. It was
`weave_core/graph/prompt.py`, shipped, on every instance: two few-shot examples
carried verbatim from the parent engine, a science-fiction short story and a
speaker sales call, and **5 of the sales example's entities were real nodes in
the demo graph** out of 924.

**The leak is the symptom; the domain mismatch is the defect.** Weave reads PRDs,
ADRs, tasks and reviews, and its extractor was being calibrated on a novel and a
price objection. This is the half-rebrand in the one place A3 cannot reach — A3
bans two *spellings*, and nothing in the contract said the prompts must be about
the domain the product serves.

**What is asserted here, and what is not.** These tests read the prompt: they
check that the examples are about software delivery, that their declared types
are the ontology's, and that the parent's worked examples are gone. They cannot
check what a model *does* with them — that needs a model, and it is
`scripts/measure_extraction.py`, whose numbers are the R2 half of this change.

**Three example blocks, not two.** The plan named `entity_extraction_examples`
and `cg_entity_extraction_examples`. A third — `cg_entity_extraction_json_examples`,
inside the JSON-mode prompt — carried the same sales conversation and the same
Barack Obama excerpt. The tests below iterate over *every* key containing
"example" for that reason: a sweep aimed at the two blocks somebody listed is a
sweep that reports success over two thirds of the surface.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from weave_core.graph.prompt import PROMPTS

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Worked-example content carried from the parent engine. Names, not themes —
#: each of these appeared verbatim in a shipped prompt, and five of them reached
#: a real graph.
PARENT_EXAMPLE_CONTENT = (
    # the B2B speaker sales call
    "AudioRival", "SoundMax", "TechGadgets", "Premium Wireless Speaker",
    "ClosedLost", "Competitor Pricing Objection", "Price Too High",
    # the enterprise discount conversation
    "Sarah Chen", "MegaCorp", "Salesforce", "deals-review",
    # the science-fiction story
    "Taylor", "Jordan", "Cruz", "The Device",
    # the stock-market report and the presidency
    "Nexon Technologies", "Omega Energy", "Barack Obama", "Affordable Care Act",
    # the general-knowledge keyword queries
    "deforestation", "International trade", "poverty",
)

#: Vocabulary a software-delivery example is expected to use. Not a style
#: preference: it is the difference between an extractor shown what a decision
#: record looks like and one shown what a price objection looks like.
SOFTWARE_VOCABULARY = (
    "RFC", "ADR", "TASK", "CR-", "commit", "pull request", "review",
    "module", "task", "workspace",
)


def _example_blocks() -> dict[str, str]:
    """Every example block in the prompt file, by key.

    Iterating the whole `PROMPTS` mapping rather than naming the keys: the third
    block is exactly what a named list would have missed.
    """
    blocks: dict[str, str] = {}
    for key, value in PROMPTS.items():
        if "example" not in key:
            continue
        blocks[key] = "\n".join(value) if isinstance(value, list) else str(value)
    return blocks


def test_there_are_example_blocks_to_check():
    """The premise. A filter that matched nothing would make the rest vacuous —
    and the count is asserted because the third block is the point."""
    blocks = _example_blocks()
    assert len(blocks) >= 4, f"only found {sorted(blocks)}"


def test_no_parent_worked_example_survives_anywhere_in_the_prompt():
    """**The whole file, not the example blocks.**

    Scoped wider than the thing being fixed on purpose: the JSON-mode examples
    lived inside a system-prompt string rather than in a list, so a check that
    looked only where the examples were *expected* would have passed while the
    sales conversation was still shipping.
    """
    text = (_REPO / "weave_core" / "graph" / "prompt.py").read_text(encoding="utf-8")
    survivors = [name for name in PARENT_EXAMPLE_CONTENT if name in text]
    assert not survivors, (
        "the parent engine's worked examples are still in the shipped prompt: "
        + ", ".join(survivors)
    )


@pytest.mark.parametrize("key", sorted(_example_blocks()))
def test_each_example_block_teaches_software_delivery(key):
    """Every block, so none is left calibrated on something else."""
    block = _example_blocks()[key]
    hits = [word for word in SOFTWARE_VOCABULARY if word.lower() in block.lower()]
    assert len(hits) >= 3, (
        f"{key} does not read like a software-delivery example — found only "
        f"{hits}. An extractor is taught what an entity looks like by what it is "
        "shown."
    )


def test_the_declared_types_are_the_ontology_s():
    """`competitor` and `objection` were entity types the parent's example
    declared. Weave's ontology has neither, so the extractor was being told to
    look for things this product does not model."""
    from weave.team import preset

    ontology = {o["name"].lower()
                for o in preset.load_part("ontology")["object_types"]}
    declared: set[str] = set()
    for block in _example_blocks().values():
        for match in re.findall(r"entity\{tuple_delimiter\}[^{]+\{tuple_delimiter\}([^{]+)\{",
                                block):
            declared.add(match.strip().lower())
        for match in re.findall(r'"entity_type":\s*"([^"]+)"', block):
            declared.add(match.strip().lower())

    assert declared, "no entity types are declared by any example"
    # **The carve-out is `{other}`, and its shape was measured, not argued** (W50).
    #
    # `other` is exempt because the ontology names it as the escape hatch. Nothing
    # else is, and three of the four ways to get there were tried on 22 documents,
    # three runs a side, gpt-4o-mini — invented nodes excluded from both sides:
    #
    #   A  unchanged                        ontology-or-Other 66.5%   answerable 42.5%
    #   B1 the five hardcoded type lists    ontology-or-Other 73.3%   answerable 45.0%
    #   B2 + `Other` demonstrated in the
    #      examples                         ontology-or-Other 99.0%   answerable 27.8%
    #   C  off-ontology entities removed
    #      from the examples entirely       ontology-or-Other 86.6%   answerable 46.8%
    #
    # **B2 is the trap and it is why this comment is long.** Re-typing the
    # examples' off-ontology entities to `Other` scores almost perfectly on
    # *"every type is one the ontology declares, or `Other`"* and **collapses the
    # answer surface by a third** — `Other` nodes went 68 → 199. `Other` is legal
    # and *unanswerable*: the four questions search by ontology type. The metric
    # improved while the product got worse.
    #
    # C is what shipped. The examples no longer *demonstrate* the escape hatch,
    # they demonstrate **selectivity** — an entity with no ontology home is not
    # extracted. Conformance rises (disjoint across three runs), answerability is
    # the best of the four, and `extracted_nodes` held at 266 vs 281, so the
    # feared opposite failure — under-extraction — did not appear.
    #
    # Anything added here needs the same evidence, on both columns.
    unknown = declared - ontology - {"other"}
    assert not unknown, (
        f"the examples declare entity types Weave's ontology does not define: "
        f"{sorted(unknown)}. Adding to the carve-out needs a measurement, not an "
        f"argument — see W50."
    )


def test_the_json_examples_are_valid_json():
    """They are shown to a model as the shape of a correct answer. One with a
    stray escape teaches a broken shape — and the first draft of this change had
    exactly that, an escaped quote that Python consumed on import."""
    for block in PROMPTS["cg_entity_extraction_json_examples"]:
        payload = json.loads(block.split("<Output>", 1)[1].strip())
        assert payload["entities"] and payload["relationships"]


def test_the_delimiter_examples_still_format():
    """They pass through `.format()`, so a single brace in an example is a
    `KeyError` at extraction time rather than at import."""
    for key in ("entity_extraction_examples", "cg_entity_extraction_examples"):
        for block in PROMPTS[key]:
            block.format(tuple_delimiter="|", completion_delimiter="<C>",
                         entity_types="X", language="English")


# ── the harness ──────────────────────────────────────────────────────────────


def test_the_harness_reads_its_leak_list_from_the_live_prompt():
    """**Not the five names we happened to find.**

    A hard-coded list would pass the day somebody writes a new example with new
    entities — which is precisely the change this phase makes. The harness parses
    the current prompt, so it follows whatever the examples say.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "measure_extraction", _REPO / "scripts" / "measure_extraction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = module.example_entity_names()
    assert names, "the harness finds no example entities at all"
    # It must see both encodings: the delimiter form and the JSON form.
    assert "RFC-014" in names, "the delimiter-form examples are not being read"
    assert "ADR-007" in names, "the JSON-form examples are not being read"
    assert not set(names) & set(PARENT_EXAMPLE_CONTENT)


def test_the_harness_separates_a_copied_identifier_from_a_shared_word():
    """A cost of the fix, made explicit.

    The old examples leaked *visibly*: "AudioRival" in a software graph is
    unmistakable. Examples drawn from the domain the product serves are harder to
    catch, because "PostgreSQL" is exactly what a real document produces. So the
    gate turns on identifiers — `RFC-014`, a commit sha, a run id — which cannot
    arrive any other way, while the report still lists every overlap for a human.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "measure_extraction", _REPO / "scripts" / "measure_extraction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.distinctive(["RFC-014", "PostgreSQL", "auth", "TASK-221"]) == [
        "RFC-014", "TASK-221"]
    assert module.distinctive(["PostgreSQL"]) == []


def test_the_harness_borrows_the_products_engine():
    """**W37: the measuring path had never executed anywhere.**

    It built `WeaveGraph(working_dir=…)` with no embedding function and died on
    `embedding_func is required for vector storage` before reading a document —
    while `--names-only` and `--compare` worked, which made it read like a
    harness that ran. *The paths that were exercised sat next to the path that
    mattered.*

    It now constructs the server's own app and takes the engine from
    `app.state.workspace_pool`, so the backends come from the same
    `WEAVE_EMBEDDING_*` and `WEAVE_LLM_*` variables the server reads. A harness
    that wired its own embedding model would produce numbers incomparable to
    anything the product produces, which is the one thing a baseline must not be.
    """
    import ast

    source = (_REPO / "scripts" / "measure_extraction.py").read_text(encoding="utf-8")
    assert "app.state" in source and "workspace_pool" in source

    # **Parsed, not grepped** — the sixth guard of mine to flag the docstring
    # that *explains* a fix as though it were the fix's absence. The prose above
    # names `WeaveGraph(working_dir=…)` precisely because the code no longer
    # calls it.
    constructs = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "WeaveGraph"
    ]
    assert not constructs, (
        "the harness constructs a bare engine again, with no embedding backend"
    )

    from weave.server import app as app_module

    assert "app.state.workspace_pool = workspace_pool" in \
        pathlib.Path(app_module.__file__).read_text(encoding="utf-8"), (
            "the server no longer publishes the pool, so the harness cannot "
            "borrow the product's engine"
        )


def test_an_empty_extraction_is_not_reported_as_a_clean_baseline():
    """The second half of W37, and the sharper one.

    With the backend unreachable, extraction logs its failure and returns — and
    the harness then printed `entities: 0, leaked: 0` and exited **0**. A green
    result from a run in which nothing was extracted is exactly the defect this
    harness exists to measure, occurring in the harness.
    """
    source = (_REPO / "scripts" / "measure_extraction.py").read_text(encoding="utf-8")
    assert "NOT A MEASUREMENT" in source
    assert 'report["entities"] == 0' in source, (
        "a run that extracts nothing is no longer distinguished from a run that "
        "found nothing to extract"
    )
