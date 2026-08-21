"""Hand-written lists are read from the ontology instead (P15, D-050, W39/W42/W44).

Weave installs an ontology as signed governance — the object types the whole
answer surface is built on — and several places kept a list by hand instead.
Each divergence had a measured consequence:

    DEFAULT_ENTITY_TYPES (14)   vs the ontology (18)   zero overlap; nothing
                                                       extracted is answerable
    CONTENT_FIELDS (12)         vs what nodes carry     `description` dropped,
                                                       and 947 of 975 carry one
    ARTIFACT_TYPES (10)         vs the ontology         checked below — the list
                                                       was right, the claim was not
    a six-key dict in `create`   vs what the caller sent every locator discarded

**The fourth is the one in a write path**, and the worst: `acreate_entity` built
`node_data` by hand and dropped everything else *without a warning*, so every
`Feature`, `ChangeRequest` and `ArchitectureDecisionRecord` created through
`/graph/entity/create` had no locator at all. `aedit_entity` merges, which is
what hid it — anything written twice looked correct.

**On the noun** (the manager's rule, after five defects guarded by passing
tests): each assertion below names the same thing its criterion does. Where the
criterion is about a *node*, the test writes a node and reads it back; where it
is about a *list*, it compares lists. What none of these can do is prove what a
model extracts — that needs a model, and it is the M15 gate.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ontology():
    from weave.team import preset

    return preset.load_part("ontology")["object_types"]


# ── W44 · the write path keeps what it was given ─────────────────────────────


@pytest.mark.asyncio
async def test_creating_an_entity_keeps_its_locator():
    """**The criterion is about a node, so this calls the function and reads the
    node back.**

    `acreate_entity` assembled a six-key dict — `entity_id`, `entity_type`,
    `description`, `source_id`, `file_path`, `created_at` — and silently
    discarded every other key the caller passed, including all four `locator_*`
    fields. A5 says an artifact references its source by `repo · path · rev`;
    created this way it referenced nothing.

    **The first version of this test built the merged dict itself and asserted
    on that** — it proved a dict comprehension, not the function, in the very
    file whose docstring says to name the same noun as the criterion. Only the
    graph and the vector store are stubbed, because embedding needs a model and
    has nothing to do with which keys survive.
    """
    from weave_core.graph.query import acreate_entity
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)   # the create path takes a keyed lock
    written: dict = {}

    class _Graph:
        async def has_node(self, name):
            return name in written

        async def upsert_node(self, name, data):
            written[name] = dict(data)

        async def get_node(self, name):
            return written.get(name)

        async def index_done_callback(self):
            pass

    class _Vdb:
        """Permissive on purpose: every method beyond `upsert` is bookkeeping
        around the write, and none of it decides which keys survive."""

        global_config: dict = {"workspace": ""}

        async def upsert(self, data):
            pass

        async def get_by_id(self, key):
            return None

        async def index_done_callback(self):
            pass

    await acreate_entity(
        _Graph(), _Vdb(), _Vdb(), "RFC-014",
        {
            "entity_type": "RFC",
            "description": "Outbound-only dev hosts",
            "title": "RFC-014",
            "status": "approved",
            "locator_repo": "Weave",
            "locator_path": "docs/WEAVE_RFC.html",
            "locator_rev": "b70b78c5",
            "locator_anchor": "#outbound",
        },
    )

    node = written["RFC-014"]
    assert node["locator_path"] == "docs/WEAVE_RFC.html", (
        "the locator was discarded, so this artifact references nothing (A5)"
    )
    assert node["locator_repo"] == "Weave"
    assert node["locator_rev"] == "b70b78c5"
    assert node["locator_anchor"] == "#outbound"
    assert node["status"] == "approved", "non-locator fields were dropped too"
    # The fields the function computes still win over anything sent in.
    assert node["entity_id"] == "RFC-014"
    assert node["source_id"] == "manual_creation"


def test_the_two_write_paths_agree_about_what_survives():
    """`edit` merged and `create` did not, which is exactly why this hid: a node
    written twice looked correct, so only a *first* write lost anything."""
    import inspect

    from weave_core.graph.query import _edit_entity_impl, acreate_entity

    create = inspect.getsource(acreate_entity)
    # `aedit_entity` delegates; the merge lives in the implementation it calls,
    # which is where the asymmetry actually was.
    edit = inspect.getsource(_edit_entity_impl)
    assert "**node_data, **updated_data" in edit
    assert "(entity_data or {}).items()" in create, (
        "create builds its node from a fixed key list again, so a first write "
        "drops whatever the caller sent"
    )


# ── W42 · the rot check looks in the right place, at the right types ─────────


def test_check_locators_defaults_to_the_shared_working_directory():
    """*"resolved: 0 · dangling: 0"* from a directory it never looked in — a
    clean bill from an empty inspection, on CR-002's own acceptance gate. W27's
    split default surviving in a script the sweep missed (D-048)."""
    source = (_REPO / "scripts" / "check_locators.py").read_text(encoding="utf-8")
    assert "resolve_working_dir()" in source
    assert '"--working-dir", default="./weave_storage"' not in source


def test_the_artifact_types_are_the_ontology_s_and_the_hand_list_was_right():
    """**The measurement, including the part that corrects the report.**

    W42 was raised as *"8 types invisible to the rot check"* — 10 hand-written
    against 18 in the ontology. Measured, the ontology declares `locator_*`
    properties on exactly **10** types, and they are precisely the ten that were
    listed. The hand-written set was accurate; the eight without locators are
    correctly excluded, because a `Worker` or an `Environment` holds no document.

    Deriving it is still right — a list that cannot drift beats a list that
    happens to be correct today — but the defect was the working directory, not
    the type set, and saying otherwise would leave a fixed number in the record
    that was never true.
    """
    module = _script("check_locators")
    declares_locator = {
        t["name"] for t in _ontology()
        if any(str(p.get("name", "")).startswith("locator_")
               for p in t.get("properties", []))
    }
    assert module.ARTIFACT_TYPES == declares_locator
    assert len(declares_locator) == 10
    assert len(_ontology()) == 18
    for name in ("Worker", "Environment", "PullRequest", "Role"):
        assert name not in declares_locator, (
            f"{name} declares no locator, so requiring one of it would report "
            "rot that does not exist"
        )


# ── W39 · the answer carries what nodes actually hold ────────────────────────


def test_an_extracted_node_is_named_by_its_description():
    """947 of 975 nodes carry a `description` and the answer passed none of them
    through, so an extracted node reached the UI with nothing to show and
    rendered as a bare id."""
    from weave.model.answers import CONTENT_FIELDS, LABEL_FIELDS, _node_view

    assert "description" in CONTENT_FIELDS and "description" in LABEL_FIELDS

    view = _node_view("chunk-7f2a", {
        "entity_type": "Concept",
        "description": "Outbound-only is the property that hosts initiate every connection.",
    })
    assert view["label"].startswith("Outbound-only")
    assert view["description"].startswith("Outbound-only")


def test_an_authored_title_still_wins_over_the_extractors_prose():
    """`description` is last in the chain: it names a node only when nothing a
    person wrote does."""
    from weave.model.answers import _node_view

    view = _node_view("RFC-014", {
        "entity_type": "RFC", "title": "Outbound-only dev hosts",
        "description": "a much longer machine-written paragraph",
    })
    assert view["label"] == "Outbound-only dev hosts"


# ── the harness reports the number this phase moves ─────────────────────────


def test_the_harness_counts_answerable_nodes():
    """Entity counts said how much was extracted and nothing about whether any of
    it could be *answered*. 92 nodes, not one Weave type, `/ask/features` empty —
    the extractor productive and the answer surface blind to all of it."""
    module = _script("measure_extraction")

    assert len(module._ontology_types()) == 18
    text = module.compare(
        {"entities": 36, "relations": 20, "leaked_count": 0,
         "answerable_nodes": 0, "answerable_pct": 0.0},
        {"entities": 40, "relations": 22, "leaked_count": 0,
         "answerable_nodes": 31, "answerable_pct": 77.5})
    assert "answerable nodes" in text and "0.0% → 77.5%" in text


def test_the_harness_does_not_keep_its_own_copy_of_the_vocabulary():
    """A fourth hand-written list, inside the harness measuring the first three,
    would be this phase's defect in its own instrument."""
    source = (_REPO / "scripts" / "measure_extraction.py").read_text(encoding="utf-8")
    assert 'preset.load_part("ontology")' in source
    assert "DEFAULT_ENTITY_TYPES" not in source


# ── and the chain itself ─────────────────────────────────────────────────────


def test_the_parent_list_is_out_of_the_extraction_chain():
    """With nothing set, the server contributes **no** types and the chain runs.

    **Asserted on behaviour**, not on a spelling: the first version matched the
    literal `get_env_value("WEAVE_ENTITY_TYPES", [], list)` and broke the moment
    that line was replaced by a shared parser — the property was still true and
    the test still failed. A test coupled to how a line is written reports a
    refactor as a defect.
    """
    import sys

    from weave.server import config as config_module

    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        for name in [m for m in list(sys.modules) if m.startswith("weave.server.config")]:
            del sys.modules[name]
        from weave.server.config import parse_args

        assert parse_args().entity_types == [], (
            "the server injects a default entity-type list, so the workspace's "
            "installed ontology never gets a say"
        )
    finally:
        sys.argv = argv

    source = pathlib.Path(config_module.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_ENTITY_TYPES" not in source.replace(
        "# This defaulted to `DEFAULT_ENTITY_TYPES`", ""), (
        "the parent engine's fourteen are back in the chain"
    )


def test_the_resolver_is_called_per_run_not_captured():
    """**The one that must not be got wrong** (A8). Types read once go stale the
    moment a new ontology is signed, and every test you would think to write
    still passes — a test builds an engine and extracts in the same breath."""
    import ast

    from weave_core.graph import quadruple

    source = pathlib.Path(quadruple.__file__).read_text(encoding="utf-8")
    assert 'addon_params"].get("entity_types_resolver")' in source
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "resolver"
    ]
    assert calls, "the resolver is stored and never called"


def test_the_garbage_filter_is_not_cached_across_a_re_sign():
    """The same staleness one layer down: the filter was cached on the engine, so
    a re-signed ontology reached extraction and not the filter."""
    import inspect

    from weave_core.graph.quadruple import WeaveGraph

    source = inspect.getsource(WeaveGraph._node_filter)
    assert "self._node_filter_cache = NodeFilter" not in source
    assert "entity_types_resolver" in source, (
        "the filter's fallback is not the resolver's types, so closed-world mode "
        "would quarantine exactly the nodes the new taxonomy gets right"
    )


# ── the override is documented, and does something ───────────────────────────


def test_the_override_is_on_the_help_surface():
    """CR-003 called it *"env override, undocumented"*, and it was not even an
    argparse flag — so it appeared in no `--help` anywhere."""
    import contextlib
    import io
    import sys

    argv = sys.argv
    sys.argv = ["weave-server", "--help"]
    buffer = io.StringIO()
    try:
        from weave.server.config import parse_args

        with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit):
            parse_args()
    finally:
        sys.argv = argv

    text = buffer.getvalue()
    assert "--entity-types" in text
    assert "installed ontology" in text, (
        "the help does not say what happens when it is left unset, which is the "
        "part an operator needs"
    )


def test_the_documented_flag_actually_sets_something():
    """**Caught by running it, not by reading it.**

    The flag was added and a later line unconditionally overwrote it from the
    environment — a documented option, present in `--help`, that did nothing.
    That is W20's family: advice naming a control that has no effect.
    """
    import sys

    argv = sys.argv
    sys.argv = ["weave-server", "--entity-types", "Alpha,Beta"]
    try:
        for name in [m for m in list(sys.modules) if m.startswith("weave.server.config")]:
            del sys.modules[name]
        from weave.server.config import parse_args

        assert parse_args().entity_types == ["Alpha", "Beta"]
    finally:
        sys.argv = argv


@pytest.mark.parametrize("raw,expected", [
    ("PRD,RFC", ["PRD", "RFC"]),
    ('["PRD","RFC"]', ["PRD", "RFC"]),
    (" PRD , RFC ", ["PRD", "RFC"]),
    ("[PRD, RFC]", ["PRD", "RFC"]),
    ("", []),
])
def test_one_parser_reads_the_override(raw, expected):
    """**Two readings of one variable** is this phase's theme in miniature.

    `get_env_value(..., list)` wanted JSON and warned-then-ignored anything else;
    the resolver split on commas. So `WEAVE_ENTITY_TYPES=PRD,RFC` worked on one
    path and silently did nothing on the other — and D-050's rollback
    instruction, *"set WEAVE_ENTITY_TYPES to the old list"*, was a trap on
    whichever path the operator happened to be on.
    """
    from weave.model.entity_types import explicit_entity_types

    assert explicit_entity_types({"WEAVE_ENTITY_TYPES": raw}) == expected


# ── W46 · the questions match the types the extractor writes ─────────────────


class _TypedGraph:
    """A graph holding nodes typed the way a real extraction types them."""

    def __init__(self, nodes):
        self.nodes = dict(nodes)
        self.edges: dict = {}

    async def get_all_labels(self):
        return sorted(self.nodes)

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def get_node_edges(self, node_id):
        return self.edges.get(node_id, [])


@pytest.mark.asyncio
async def test_the_questions_answer_a_graph_the_extractor_wrote():
    """**The criterion, and the noun is a question's answer.**

    Every layer we built passed — the resolver returned the ontology's list per
    run, a re-signed ontology changed it without a restart, the prompt carried
    it and the model obeyed it — and `/ask/features`, `/ask/changes` and
    `/ask/learnings` all returned **zero** from a graph of 56 real nodes.

    `operate.py` did `entity_type.replace(" ", "").lower()`, inherited from an
    engine where nothing ever matched on type, while the answer surface compares
    the ontology's capitalised names on equality. The `Role` row proved it: four
    capitalised from bootstrap and one lower-case from extraction, in one graph,
    as two different types.

    Nothing short of a real model run could have found it, which is why this
    test uses the casing a real run produced rather than the casing we expect.
    """
    from weave.model.answers import ask_changes, ask_features, ask_learnings

    graph = _TypedGraph({
        # exactly as extraction wrote them before the fix
        "CR-009": {"entity_type": "changerequest", "description": "outbound-only hosts"},
        "TASK-221": {"entity_type": "task", "description": "the registry"},
        "F-1": {"entity_type": "feature", "description": "dev-host fleet"},
        "I-1": {"entity_type": "insight", "description": "a guard's reach"},
        "R-1": {"entity_type": "review", "description": "flagged"},
        # and as bootstrap writes them
        "Role:manager": {"entity_type": "Role", "description": "the manager role"},
    })

    # The counts are what each question's own seeding produces on this graph,
    # not a round number: `ask_changes` seeds on `ChangeRequest` and there are no
    # edges here to walk, so one is the correct answer — and zero was the defect.
    assert (await ask_features(graph))["count"] == 1, "/ask/features answers nothing"
    assert (await ask_changes(graph))["count"] == 1, "/ask/changes answers nothing"
    assert (await ask_learnings(graph))["count"] == 2, "/ask/learnings answers nothing"


@pytest.mark.asyncio
async def test_both_casings_are_one_type():
    """The `Role` row: two spellings of one type in one graph. A question must
    not answer half of it."""
    from weave.model.answers import ask_learnings

    graph = _TypedGraph({
        "I-1": {"entity_type": "insight", "statement": "extracted"},
        "I-2": {"entity_type": "Insight", "statement": "written by the coordinator"},
    })
    assert (await ask_learnings(graph))["count"] == 2


def test_the_extractor_no_longer_destroys_the_ontology_s_spelling():
    """The prompt hands the model `ChangeRequest`; storing `changerequest` threw
    away the one thing that made it answerable."""
    import inspect

    from weave_core.graph.operate import _handle_single_entity_extraction

    source = inspect.getsource(_handle_single_entity_extraction)
    assert '.replace(" ", "")' in source
    assert '.lower()' not in source, (
        "extraction lower-cases the entity type again, so nothing it writes "
        "matches the ontology the answer surface queries"
    )


def test_normalising_is_one_function_used_by_both_sides():
    """The manager's instruction, and the reason it is a *key* rather than a
    canonical spelling: canonicalising needs the ontology, which lives above
    `weave_core` (A2) — the same boundary the resolver already had to respect."""
    import inspect

    from weave.model import answers
    from weave_core.utils import normalize_type

    assert normalize_type("ChangeRequest") == normalize_type("changerequest")
    assert normalize_type(" Change Request ") == normalize_type("ChangeRequest")
    assert normalize_type(None) == ""

    source = inspect.getsource(answers)
    assert source.count("normalize_type(") >= 4, (
        "a comparison site in answers.py still matches raw strings"
    )


# ── W47(b) · the placeholders are declared, and counted apart ────────────────


def test_the_pipeline_s_placeholder_types_are_declared_in_one_place():
    """`UNKNOWN` and `ENTITY` are one condition under two inherited spellings.

    Neither is a model's judgement and neither is an ontology type: both mean *a
    node had to exist because something referred to it* — `operate.py` when a
    relation names an entity no entity record described, `emit_decision_trace`
    when an audit edge has a missing endpoint. Declaring them together is what
    lets the measurement tell *what we invented* from *what the model got
    wrong*.
    """
    from weave_core.constants import PLACEHOLDER_ENTITY_TYPES

    assert PLACEHOLDER_ENTITY_TYPES == frozenset({"UNKNOWN", "ENTITY"})

    # **Legacy now, and that is the point.** The pipeline no longer writes
    # either — invented endpoints are typed `Other` and marked — but graphs
    # written before that still hold them, so both spellings must stay
    # recognised or those workspaces measure wrong.
    from weave_core.utils import normalize_type

    assert {normalize_type(t) for t in PLACEHOLDER_ENTITY_TYPES} == {"unknown", "entity"}


def test_the_harness_separates_our_placeholders_from_the_model_s_words():
    """**Two failures, two numbers** (W47).

    50% answerable was one number covering two unrelated problems: a model
    returning `concept` against a correct instruction, and endpoints the
    pipeline invented for edges nobody described. They have different owners and
    different fixes, and a single figure cannot be acted on by either.
    """
    module = _script("measure_extraction")

    text = module.compare(
        {"entities": 76, "relations": 40, "leaked_count": 0, "answerable_nodes": 0,
         "answerable_pct": 0.0, "off_ontology_nodes": 61, "placeholder_nodes": 15},
        {"entities": 76, "relations": 40, "leaked_count": 0, "answerable_nodes": 38,
         "answerable_pct": 50.0, "off_ontology_nodes": 23, "placeholder_nodes": 15,
         "invented_nodes": 15, "extracted_nodes": 61})
    assert "off-ontology" in text and "invented" in text
    assert "the model's word, not ours" in text
    assert "endpoints we conjured, not extracted" in text


def test_the_answerable_count_matches_the_way_questions_compare():
    """It counts through `normalize_type`, like the answer surface. A stricter
    count would report a number the product cannot deliver — and a looser one
    would claim nodes the questions never return."""
    source = (_REPO / "scripts" / "measure_extraction.py").read_text(encoding="utf-8")
    assert "normalize_type(kind) in answerable_types" in source


# ── W47(b) · invented endpoints are legal, marked, and never win a merge ─────


def test_an_invented_endpoint_is_ontology_legal_and_says_it_was_invented():
    """`UNKNOWN` and `ENTITY` were words the ontology never declared, so a node
    the pipeline conjured was off-schema *and* untyped. `Other` is the
    ontology's own answer to "none of these apply" — legal, and no lookup, which
    keeps it inside A2 the way `normalize_type` did."""
    from weave_core.constants import (
        INVENTED_DECISION_ENDPOINT, INVENTED_MARKER, INVENTED_RELATIONSHIP_ENDPOINT,
        OTHER_ENTITY_TYPE,
    )

    operate = (_REPO / "weave_core" / "graph" / "operate.py").read_text(encoding="utf-8")
    quadruple = (_REPO / "weave_core" / "graph" / "quadruple.py").read_text(encoding="utf-8")

    assert '"entity_type": "UNKNOWN",\n                "file_path"' not in operate
    assert '"entity_type": "ENTITY"' not in quadruple
    assert operate.count(f"{INVENTED_MARKER}: {INVENTED_RELATIONSHIP_ENDPOINT!s}") >= 0
    assert "INVENTED_RELATIONSHIP_ENDPOINT" in operate
    assert "INVENTED_DECISION_ENDPOINT" in quadruple
    assert OTHER_ENTITY_TYPE == "Other" and INVENTED_DECISION_ENDPOINT


@pytest.mark.parametrize("placeholder", ["UNKNOWN", "ENTITY", "Other", "other"])
def test_a_placeholder_never_displaces_a_type_something_asserted(placeholder):
    """**The only part of W47 that is a correctness bug.**

    `WEAVE_ENTITY_TYPES` was extracted as `concept` in one chunk and conjured as
    a bare endpoint in another, and whichever write landed second decided the
    type. Filtered rather than ordered, so the guarantee holds however the
    chunks interleave — which matters because I could not reproduce the
    interleaving without a model.
    """
    from weave_core.graph.operate import _is_placeholder_type

    assert _is_placeholder_type(placeholder)
    assert not _is_placeholder_type("ChangeRequest")
    assert not _is_placeholder_type("concept"), (
        "an off-ontology word the model chose is still an assertion — treating "
        "it as a placeholder would let a conjured node overwrite it"
    )


@pytest.mark.parametrize("candidates,existing,expected", [
    (["UNKNOWN", "concept"], None, "concept"),      # a real type beats a placeholder
    (["Other"], "RFC", "RFC"),                      # …and so does one already there
    ([], "RFC", "RFC"),                             # an empty vote keeps what exists
    (["UNKNOWN"], None, "UNKNOWN"),                 # nothing better available
    ([], None, "Other"),                            # nothing at all → the legal escape
    (["ChangeRequest"], "Other", "ChangeRequest"),  # an asserted type displaces a placeholder
])
def test_the_merge_resolves_the_type(candidates, existing, expected):
    """**Both rules, because a control showed only one was tested.**

    Filtering the vote is not enough on its own: the vote can be empty and fall
    through to whatever the node already carried. Removing the second guard
    failed nothing until this existed — so the belt-and-braces half of the
    correctness fix was unprotected, which is the same shape as everything else
    this fortnight.
    """
    from weave_core.graph.operate import _resolve_entity_type

    assert _resolve_entity_type(candidates, existing) == expected


def test_the_denominator_is_what_extraction_produced():
    """**The measurement error that got published.**

    The percentage was over every node in the graph — including 15 the pipeline
    conjured and bootstrap wrote, none of them extraction output. A figure over
    that denominator flatters or punishes the extractor for work it never did.
    """
    module = _script("measure_extraction")

    assert module._was_invented({"created_as": "relationship_endpoint"})
    assert module._was_invented({"entity_type": "UNKNOWN"}), (
        "a graph written before the marker existed must still measure correctly"
    )
    assert not module._was_invented({"entity_type": "RFC"})

    # The denominator itself, not numbers this test handed to `compare()`.
    assert module.extracted_denominator(76, 15) == 61
    assert module.extracted_denominator(15, 15) == 0
    assert module.extracted_denominator(3, 9) == 0, "a denominator cannot go negative"

    text = module.compare(
        {"entities": 76, "answerable_nodes": 0, "leaked_count": 0, "relations": 40,
         "invented_nodes": 15, "extracted_nodes": 61, "answerable_pct_of_extracted": 0.0},
        {"entities": 76, "answerable_nodes": 38, "leaked_count": 0, "relations": 40,
         "invented_nodes": 15, "extracted_nodes": 61, "answerable_pct_of_extracted": 62.3})
    assert "invented" in text and "extracted (denom.)" in text
    assert "62.3%" in text


# ── W48 · the instrument refuses what it cannot support ──────────────────────


def _agg(module, values, docs=2):
    base = dict(entities=61, relations=40, leaked_count=0, answerable_nodes=38,
                invented_nodes=15, extracted_nodes=61, documents=["d"] * docs)
    return module.aggregate([{**base, module.HEADLINE: v} for v in values])


def test_the_real_numbers_are_reported_as_not_resolvable():
    """**The measured case, run through the instrument** (W48).

    Two runs of the *unchanged* prompt scored 73.1 and 56.7 — sixteen points
    apart from per-run model sampling alone — and a prompt change was then judged
    on a one-point difference in means. The number looked like a measurement and
    was noise.
    """
    module = _script("measure_extraction")

    before = _agg(module, [73.1, 56.7])
    after = _agg(module, [53.1, 75.5, 52.9, 73.1])

    assert not module.resolvable(before, after)
    text = module.compare(before, after)
    assert "NOT RESOLVABLE" in text
    assert "56.7–73.1" in text, "the spread is not shown, so a reader cannot judge"


def test_a_difference_bigger_than_the_noise_is_reported_as_one():
    """A rule that only ever refuses is not a rule. Tight runs, large gap."""
    module = _script("measure_extraction")

    assert module.resolvable(_agg(module, [20.0, 22.0, 21.0]),
                             _agg(module, [70.0, 72.0, 71.0]))
    text = module.compare(_agg(module, [20.0, 22.0, 21.0]),
                          _agg(module, [70.0, 72.0, 71.0]))
    assert "exceeds the spread" in text and "NOT RESOLVABLE" not in text


def test_the_headline_is_the_mean_not_the_last_run():
    """One number for one thing.

    `aggregate` merges the final run's report for its counts, which left the
    per-run line showing 73.1 while the summary said 64.9 — two figures for one
    quantity on the same screen, which is the defect this instrument exists to
    find.
    """
    module = _script("measure_extraction")

    merged = _agg(module, [73.1, 56.7])
    assert merged[module.HEADLINE] == 64.9
    assert merged[f"{module.HEADLINE}_mean"] == 64.9
    assert f"  answerable/extracted  64.9%" in module.compare(merged, merged)


def test_a_single_run_still_says_it_is_a_single_run():
    """Nothing forces `--repeat`, so the common case must not read as a
    measurement it is not."""
    module = _script("measure_extraction")

    merged = _agg(module, [64.0])
    assert merged["runs"] == 1
    assert merged[f"{module.HEADLINE}_min"] == merged[f"{module.HEADLINE}_max"]
    text = module.compare(merged, _agg(module, [65.0]))
    assert "1 run(s)" in text
    assert "NOT RESOLVABLE" in text, (
        "with one run per condition the spread is zero and any difference looks "
        "real — the one case where a naive rule is most dangerous"
    )


def test_how_much_was_measured_is_reported():
    """*A count is only evidence when you know what was inspected* — the same
    rule as W42's clean bill from an unopened directory, applied to a corpus."""
    module = _script("measure_extraction")

    text = module.compare(_agg(module, [64.0], docs=22), _agg(module, [65.0], docs=22))
    assert "over 22 docs" in text


# ── W49 · --repeat runs in one event loop, and says what it managed ──────────


def _args(**over):
    import argparse

    base = dict(working_dir="/tmp/w49", chars=100, repeat=2, no_quadruple=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_repeat_runs_every_run_in_one_event_loop():
    """**The defect only exists at `--repeat 2`, and 2 is not the default.**

    `weave_core`'s storage locks are module-level `asyncio.Lock`s bound to the
    loop that created them. Calling `asyncio.run()` per run gave each run its own
    loop, and run 2 died ten documents in with *"is bound to a different event
    loop"* — after run 1 had reported success. `--repeat 1` worked, so the suite
    and every manual check agreed with me.

    This executes two runs and asserts they shared a loop, because asserting on
    a plan to run twice is what let the first version through.
    """
    import asyncio

    module = _script("measure_extraction")
    loops = []

    async def _fake_measure(corpus, working_dir, chars, quadruple=True, workspace='measure'):
        loops.append(id(asyncio.get_running_loop()))
        return {"entities": 1, "documents": ["d"], module.HEADLINE: 50.0}

    module.measure = _fake_measure
    runs, failure = asyncio.run(module._repeat(["d"], _args()))

    assert failure is None
    assert len(runs) == 2, "the second run never happened"
    assert len(set(loops)) == 1, (
        "each run got its own event loop — the storage locks bind to the first "
        "and the second run dies"
    )


def test_a_crash_mid_repeat_still_reports_what_completed():
    """It died and wrote **nothing** — no partial report, no summary, a stack
    trace in a log. The absence of an `--out` file was the only signal, and only
    because someone was watching the document-pass count."""
    import asyncio

    module = _script("measure_extraction")
    calls = {"n": 0}

    async def _fails_on_the_third(corpus, working_dir, chars, quadruple=True, workspace='measure'):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("is bound to a different event loop")
        return {"entities": 1, "documents": ["d"], module.HEADLINE: 50.0}

    module.measure = _fails_on_the_third
    runs, failure = asyncio.run(module._repeat(["d"], _args(repeat=3)))

    assert len(runs) == 2, "the completed runs were thrown away with the failure"
    assert failure and "run 3 of 3" in failure
    assert "different event loop" in failure, "the reason is not carried out"


def test_a_total_failure_is_not_reported_as_a_measurement():
    """Zero completed runs must not aggregate into a confident-looking report."""
    import asyncio

    module = _script("measure_extraction")

    async def _always_fails(corpus, working_dir, chars, quadruple=True, workspace='measure'):
        raise RuntimeError("backend unreachable")

    module.measure = _always_fails
    runs, failure = asyncio.run(module._repeat(["d"], _args()))

    assert runs == [] and failure


# ── W51/W53 · the second extraction path, and repeat that actually repeats ───


def test_both_extraction_paths_consult_the_resolver():
    """**The P15 defect arriving inside P15's own fix** (W51).

    `WeaveGraph` (quadruple) reads `cg_entity_extraction_examples`;
    `WeaveEngine` reads `entity_extraction_examples`. P15 wired the resolver
    into the first only — *and* changed `args.entity_types` to default empty so
    the chain could govern. An empty list is **present rather than missing**, so
    `.get("entity_types", DEFAULT_ENTITY_TYPES)` returned `[]` and the fallback
    never fired: the prompt offered the model no types at all, and it typed
    everything `Other`.

    A4 v6 says PostgreSQL runs `WeaveEngine`, so that was 0% answerable on the
    deployment the contract calls production for records. Measured: 0.0% before,
    41.7% after, on one document.

    One implementation wired into one of two callers — the same shape as
    `normalize_type` needing both a writer and a reader.
    """
    import ast

    for module_path in ("weave_core/graph/operate.py", "weave_core/graph/quadruple.py"):
        source = (_REPO / module_path).read_text(encoding="utf-8")
        assert '"entity_types_resolver"' in source, (
            f"{module_path} does not consult the resolver, so its extraction path "
            "uses whatever static list happens to be there"
        )
        calls = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "resolver"
        ]
        assert calls, f"{module_path} stores the resolver and never calls it"


def test_an_empty_type_list_falls_back_rather_than_offering_nothing():
    """`[]` is present, so a `.get(key, default)` fallback never fires on it.
    That distinction is the whole defect, so it is asserted rather than
    remembered."""
    source = (_REPO / "weave_core" / "graph" / "operate.py").read_text(encoding="utf-8")
    assert 'global_config["addon_params"].get("entity_types") or DEFAULT_ENTITY_TYPES' in source, (
        "an empty configured list silently becomes the prompt's whole vocabulary"
    )


def test_each_repeat_run_gets_its_own_workspace():
    """**Worse than the crash it replaced** (W53).

    Separate working directories were not enough: the shared-storage namespace
    registry is process-global and keyed by *(namespace, workspace)*. So runs
    2..N of `--repeat` found run 1's documents already enqueued, extracted
    nothing, and the aggregate reported the last — empty — run.

    A crash stops. This returned a number, and only the entities-are-zero guard
    stood between it and a published figure.
    """
    import inspect

    module = _script("measure_extraction")

    assert "workspace" in inspect.signature(module.measure).parameters
    source = inspect.getsource(module._repeat)
    assert 'workspace=f"measure{index}"' in source, (
        "every repeat run shares one workspace, so only the first extracts"
    )


def test_the_harness_never_writes_scratch_into_the_repository():
    """**W52** — the default `--working-dir` was `./.measure_extraction`, so a
    run from the repo root wrote 3 MB of ingested document text into the working
    tree. The name-guard failed on it, correctly: ingested `CONSTRAINTS.md`
    contains the very strings A3 bans.

    A measurement instrument that dirties the tree it measures is a defect
    whoever runs it, and the guard catching it rather than me is the part worth
    keeping.
    """
    source = (_REPO / "scripts" / "measure_extraction.py").read_text(encoding="utf-8")
    assert '"./.measure_extraction"' not in source, (
        "the scratch default is a repo-relative path again"
    )
    assert 'tempfile.mkdtemp(prefix="measure-extraction-")' in source
    assert not list(_REPO.glob(".measure_extraction*")), (
        "scratch storage is sitting in the repository right now"
    )
