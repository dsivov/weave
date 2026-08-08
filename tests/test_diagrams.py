"""P6 — diagrams as governed, signed artifacts.

Covers the structural signature (what makes a diagram change behavioural vs
cosmetic), the versioned store, the NL→mermaid author, and the diagram kind
flowing through the Studio's propose → assess → apply gesture.
"""

from __future__ import annotations

import pytest

from weave_core.studio.diagrams import (
    Diagram,
    DiagramAuthor,
    InMemoryDiagramStore,
    JsonDiagramStore,
    signature,
    structural_problems,
    unsafe_content,
)
from weave_core.studio import DiffEngine, InMemoryStudioStore

BASE = "flowchart LR\n  a[Architect] -->|publishes| q[Queue]\n  q --> d[Developer]\n"


# ── schema / signature ──────────────────────────────────────────────────────


@pytest.mark.offline
def test_diagram_type_is_read_from_the_header():
    assert Diagram(id="x", source=BASE).diagram_type() == "flowchart"
    assert Diagram(id="x", source="sequenceDiagram\n A->>B: hi").diagram_type() == "sequenceDiagram"
    assert Diagram(id="x", source="not a diagram at all").diagram_type() == ""


@pytest.mark.offline
def test_relabelling_and_restyling_are_cosmetic():
    """Rewording a box or restyling the picture does not change what it depicts."""
    relabelled = "flowchart LR\n  a[Lead Architect] -->|signs the plan| q[Task Queue]\n  q --> d[Dev]\n"
    restyled = (
        "flowchart TD\n"
        "  %% a comment\n"
        "  a[Architect] -->|publishes| q[Queue]\n"
        "  q --> d[Developer]\n"
        "  style a fill:#f9f\n"
        "  classDef big font-size:20px\n"
        "  class a big\n"
        "  click a \"https://example.com\"\n"
    )
    assert signature(relabelled) == signature(BASE)
    assert signature(restyled) == signature(BASE)


@pytest.mark.offline
def test_redrawing_an_arrow_is_structural():
    added_edge = BASE + "  d --> a\n"
    removed_node = "flowchart LR\n  a[Architect] --> q[Queue]\n"
    retyped = "sequenceDiagram\n  a->>q: publishes\n  q->>d: hands off\n"
    assert signature(added_edge) != signature(BASE)
    assert signature(removed_node) != signature(BASE)
    assert signature(retyped) != signature(BASE)


@pytest.mark.offline
def test_er_cardinality_survives_the_edge_label_stripper():
    """`||--o{` is a connector, not a `|label|` — changing it is structural."""
    one_to_many = "erDiagram\n  CUSTOMER ||--o{ ORDER : places\n"
    one_to_one = "erDiagram\n  CUSTOMER |o--o| ORDER : places\n"
    reworded = "erDiagram\n  CUSTOMER ||--o{ ORDER : submits\n"
    assert signature(one_to_many) != signature(one_to_one)
    assert signature(one_to_many) == signature(reworded)   # the verb is a label


@pytest.mark.offline
def test_edge_chains_and_dash_labels_both_parse():
    assert signature("flowchart LR\n a --> b --> c\n")[1] == frozenset({"a-->b b-->c"})
    # `-- yes -->` is one edge with a label, not two edges through a node named "yes"
    assert signature("flowchart LR\n a -- yes --> b\n")[1] == frozenset({"a-->b"})


@pytest.mark.offline
def test_lint_rejects_prose_and_unsafe_content():
    assert "id is required" in Diagram(id="", source=BASE).lint()
    assert any("mermaid diagram type" in p for p in Diagram(id="x", source="hello there").lint())
    xss = "flowchart LR\n a --> b\n click a \"javascript:alert(1)\"\n"
    assert unsafe_content(xss) == ["source contains javascript: URL"]
    assert any("javascript:" in p for p in Diagram(id="x", source=xss).lint())
    assert any("<script>" in p for p in Diagram(id="x", source="flowchart LR\n<script>x</script>").lint())


@pytest.mark.offline
def test_structural_problems_catch_broken_mermaid():
    assert any("no nodes or edges" in p for p in structural_problems("flowchart LR\n"))
    assert any("unbalanced" in p for p in structural_problems("flowchart LR\n a[Open --> b\n"))
    assert structural_problems(BASE) == []


# ── store ───────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_store_versions_appendonly_and_keeps_old_versions():
    store = InMemoryDiagramStore()
    v1 = store.save("w", Diagram(id="arch", source=BASE, title="Arch"))
    v2 = store.save("w", Diagram(id="arch", source=BASE + "  d --> a\n", title="Arch"))
    assert (v1.version, v2.version) == (1, 2)
    assert store.get("w", "arch").version == 2                # latest by default
    assert store.get("w", "arch", 1).source == BASE           # v1 still readable
    assert store.get("w", "missing") is None


@pytest.mark.offline
def test_store_refuses_to_persist_an_invalid_diagram():
    store = InMemoryDiagramStore()
    with pytest.raises(ValueError, match="mermaid diagram type"):
        store.save("w", Diagram(id="bad", source="just prose"))
    assert store.list("w") == []


@pytest.mark.offline
def test_store_is_workspace_isolated_and_indexes_depicts():
    store = InMemoryDiagramStore()
    store.save("acme", Diagram(id="a1", source=BASE, depicts=["CR-7"]))
    store.save("acme", Diagram(id="a2", source=BASE, depicts=["CR-9"]))
    store.save("other", Diagram(id="a3", source=BASE, depicts=["CR-7"]))
    assert [d.id for d in store.list("acme")] == ["a1", "a2"]
    assert [d.id for d in store.depicting("acme", "CR-7")] == ["a1"]
    assert [d.id for d in store.list("other")] == ["a3"]


@pytest.mark.offline
def test_json_store_round_trips(tmp_path):
    store = JsonDiagramStore(str(tmp_path))
    store.save("w", Diagram(id="arch", source=BASE, title="T", depicts=["CR-1"], tags=["x"]))
    reopened = JsonDiagramStore(str(tmp_path)).get("w", "arch")
    assert reopened is not None
    assert (reopened.title, reopened.depicts, reopened.tags) == ("T", ["CR-1"], ["x"])
    assert store.delete("w", "arch") is True
    assert JsonDiagramStore(str(tmp_path)).get("w", "arch") is None


# ── NL author ───────────────────────────────────────────────────────────────


def _llm_returning(*payloads):
    """A scripted LLM: each call returns the next payload."""
    calls = {"n": 0, "prompts": []}

    async def llm(prompt, system_prompt=None, **kwargs):
        calls["prompts"].append(prompt)
        out = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return out

    return llm, calls


@pytest.mark.offline
@pytest.mark.asyncio
async def test_author_produces_a_validated_diagram():
    llm, _ = _llm_returning(
        '{"diagram": {"title": "Flow", "source": "flowchart LR\\n  a[A] --> b[B]",'
        ' "description": "d", "depicts": ["CR-1"]}, "explanation": "drew it"}')
    result = await DiagramAuthor(llm).generate("show A going to B", diagram_id="arch")
    assert result.valid
    assert result.diagram["id"] == "arch"
    assert result.diagram["depicts"] == ["CR-1"]
    assert result.explanation == "drew it"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_author_repairs_an_invalid_first_draft():
    llm, calls = _llm_returning(
        '{"diagram": {"source": "this is not mermaid"}}',
        '{"diagram": {"source": "flowchart LR\\n  a --> b"}, "explanation": "fixed"}')
    result = await DiagramAuthor(llm).generate("draw it")
    assert result.valid and result.attempts == 2
    assert "YOUR PREVIOUS ATTEMPT WAS INVALID" in calls["prompts"][1]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_author_never_returns_unsafe_source():
    llm, _ = _llm_returning(
        '{"diagram": {"source": "flowchart LR\\n a --> b\\n click a \\"javascript:x()\\""}}')
    result = await DiagramAuthor(llm).generate("draw it", max_repairs=0)
    assert not result.valid
    assert any("javascript:" in e for e in result.errors)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_author_keeps_existing_depicts_when_revising():
    llm, calls = _llm_returning(
        '{"diagram": {"source": "flowchart LR\\n a --> c"}, "explanation": "revised"}')
    result = await DiagramAuthor(llm).generate(
        "add c", current={"source": BASE, "depicts": ["CR-4"]})
    assert result.valid and result.diagram["depicts"] == ["CR-4"]
    assert "Revise this existing diagram" in calls["prompts"][0]


# ── the Studio gesture over the diagram kind ────────────────────────────────


def _engine(store=None):
    return DiffEngine(studio_store=InMemoryStudioStore(),
                      diagram_store=store or InMemoryDiagramStore())


@pytest.mark.offline
@pytest.mark.asyncio
async def test_first_version_is_behavioural_and_needs_signoff():
    store = InMemoryDiagramStore()
    engine = _engine(store)
    diff = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": BASE})
    engine.assess("w", diff)
    assert diff.behaviour_changed is True
    with pytest.raises(ValueError, match="sign-off"):
        await engine.apply("w", diff)

    result = await engine.apply("w", diff, approver="Ana", reason="initial architecture")
    assert result["version"] == 1
    assert result["sign_off"]["approver"] == "Ana"
    assert store.get("w", "arch").source == BASE


@pytest.mark.offline
@pytest.mark.asyncio
async def test_relabelling_applies_without_a_signoff_but_still_versions():
    store = InMemoryDiagramStore()
    engine = _engine(store)
    first = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": BASE})
    engine.assess("w", first)
    await engine.apply("w", first, approver="Ana", reason="initial")

    relabelled = "flowchart LR\n  a[Chief Architect] -->|signs| q[Work Queue]\n  q --> d[Dev]\n"
    diff = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": relabelled})
    engine.assess("w", diff)
    assert diff.behaviour_changed is False
    result = await engine.apply("w", diff)                    # no approver needed
    assert result["version"] == 2
    assert result["sign_off"]["approver"] == "system"
    assert len(engine.history("w", "diagram", "arch")) == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_adding_an_edge_requires_a_signoff():
    engine = _engine()
    first = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": BASE})
    engine.assess("w", first)
    await engine.apply("w", first, approver="Ana", reason="initial")

    diff = await engine.propose("w", "diagram", "arch",
                                draft={"id": "arch", "source": BASE + "  d --> a\n"})
    engine.assess("w", diff)
    assert diff.behaviour_changed is True
    with pytest.raises(ValueError, match="sign-off"):
        await engine.apply("w", diff)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_changing_what_a_diagram_depicts_is_behavioural():
    engine = _engine()
    first = await engine.propose("w", "diagram", "arch",
                                 draft={"id": "arch", "source": BASE, "depicts": ["CR-1"]})
    engine.assess("w", first)
    await engine.apply("w", first, approver="Ana", reason="initial")

    diff = await engine.propose("w", "diagram", "arch",
                                draft={"id": "arch", "source": BASE, "depicts": ["CR-2"]})
    engine.assess("w", diff)
    assert diff.behaviour_changed is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_invalid_mermaid_never_reaches_the_ledger():
    store = InMemoryDiagramStore()
    engine = _engine(store)
    diff = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": "prose"})
    engine.assess("w", diff)
    with pytest.raises(ValueError, match="mermaid diagram type"):
        await engine.apply("w", diff, approver="Ana", reason="oops")
    assert store.get("w", "arch") is None
    assert engine.history("w", "diagram", "arch") == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_revert_forward_applies_a_prior_version():
    store = InMemoryDiagramStore()
    engine = _engine(store)
    first = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": BASE})
    engine.assess("w", first)
    await engine.apply("w", first, approver="Ana", reason="initial")

    grown = BASE + "  d --> a\n"
    second = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": grown})
    engine.assess("w", second)
    await engine.apply("w", second, approver="Ana", reason="added the feedback loop")
    assert store.get("w", "arch").source == grown

    out = await engine.revert("w", "diagram", "arch", 1, approver="Ana", reason="too noisy")
    assert out["version"] == 3                                # forward-applied, not rewound
    assert store.get("w", "arch").source == BASE
    assert engine.history("w", "diagram", "arch")[-1].get("origin") == "reapproval"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_diagram_kind_is_authorable_from_a_spec():
    llm, _ = _llm_returning(
        '{"diagram": {"source": "flowchart LR\\n a[A] --> b[B]", "title": "T"},'
        ' "explanation": "drew A to B"}')
    engine = DiffEngine(studio_store=InMemoryStudioStore(),
                        diagram_store=InMemoryDiagramStore(),
                        llm_resolver=lambda ws: llm)
    out = await engine.draft("w", "diagram", "arch", "draw A going to B")
    assert out["reply"] == "drew A to B"
    assert out["diff"]["kind"] == "diagram"
    assert "flowchart" in out["diff"]["delta"]["after"]["source"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_diagram_appears_in_the_studio_artifact_list():
    engine = _engine()
    diff = await engine.propose("w", "diagram", "arch", draft={"id": "arch", "source": BASE})
    engine.assess("w", diff)
    await engine.apply("w", diff, approver="Ana", reason="initial")
    assert {"kind": "diagram", "artifact_id": "arch", "version": 1, "revisions": 1} in \
        engine.artifacts("w")
