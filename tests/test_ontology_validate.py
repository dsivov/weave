"""Tests for extraction validation against the ontology (P2). Fully offline."""

from __future__ import annotations

import pytest

from weave_core.governance.ontology import (
    Cardinality,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionValidator,
    LinkType,
    ObjectType,
    Ontology,
    Property,
    PropertyKind,
)
from weave_core.governance.ontology.validate import CONFORMS, INVALID, UNKNOWN_TYPE


@pytest.fixture
def onto() -> Ontology:
    o = Ontology(name="acme")
    o.define_object(ObjectType("Person"))
    o.define_object(
        ObjectType("Order").add(Property("value", PropertyKind.MONEY))
    )
    o.define_link(LinkType(
        "approved", source_types=["Person"], target_types=["Order"],
        cardinality=Cardinality.ONE_TO_MANY,
    ))
    return o


# ── entities ─────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_known_entity_conforms_and_coerces(onto):
    v = ExtractionValidator(onto)
    rep = v.validate(entities=[ExtractedEntity("Big Deal", "Order", {"value": "$25,000"})])
    assert rep.ok
    item = rep.items[0]
    assert item.status == CONFORMS and item.coerced["value"] == 25000.0


@pytest.mark.offline
def test_entity_bad_property_is_invalid(onto):
    v = ExtractionValidator(onto)
    rep = v.validate(entities=[ExtractedEntity("X", "Order", {"value": "not money"})])
    assert not rep.ok
    assert rep.items[0].status == INVALID and rep.items[0].errors


@pytest.mark.offline
def test_unknown_type_is_warning_open_world_but_violation_closed_world(onto):
    ent = ExtractedEntity("Acme HQ", "Building", {})
    open_rep = ExtractionValidator(onto).validate(entities=[ent])
    assert open_rep.ok                            # open-world: tolerated
    assert open_rep.items[0].status == UNKNOWN_TYPE and open_rep.items[0].warnings

    closed_rep = ExtractionValidator(onto, closed_world=True).validate(entities=[ent])
    assert not closed_rep.ok                       # closed-world: rejected
    assert closed_rep.items[0].errors
    assert closed_rep.unknown_types() == ["Building"]


# ── relations ────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_relation_domain_range_inferred_from_entity_types(onto):
    # relation dict omits endpoint types; validator infers them from the entities
    v = ExtractionValidator(onto)
    rep = v.validate(
        entities=[ExtractedEntity("Sarah", "Person"), ExtractedEntity("Deal", "Order")],
        relations=[ExtractedRelation("approved", "Sarah", "Deal")],
    )
    assert rep.ok
    assert [i.status for i in rep.items if i.kind == "relation"] == [CONFORMS]


@pytest.mark.offline
def test_relation_wrong_direction_is_invalid(onto):
    v = ExtractionValidator(onto)
    rep = v.validate(
        entities=[ExtractedEntity("Sarah", "Person"), ExtractedEntity("Deal", "Order")],
        relations=[ExtractedRelation("approved", "Deal", "Sarah")],   # Order→Person: not allowed
    )
    rel = [i for i in rep.items if i.kind == "relation"][0]
    assert not rep.ok and rel.status == INVALID


@pytest.mark.offline
def test_unknown_endpoint_type_warns_not_fails(onto):
    # relation references an entity not declared in the batch → endpoint type
    # unknown → domain can't be verified → warning, not a violation
    v = ExtractionValidator(onto)
    rep = v.validate(
        entities=[ExtractedEntity("Sarah", "Person")],       # "Deal" not declared
        relations=[ExtractedRelation("approved", "Sarah", "Deal")],
    )
    rel = [i for i in rep.items if i.kind == "relation"][0]
    assert rep.ok and rel.status == CONFORMS
    assert any("range not verified" in w for w in rel.warnings)


@pytest.mark.offline
def test_unknown_link_type(onto):
    rep = ExtractionValidator(onto, closed_world=True).validate(
        relations=[ExtractedRelation("bribed", "A", "B")])
    assert not rep.ok and rep.items[0].status == UNKNOWN_TYPE


# ── adapters over raw Weave/WeaveEngine extraction dicts ────────────────────────────


@pytest.mark.offline
def test_from_dict_adapts_cg_extraction_shapes(onto):
    entities = [
        {"entity_name": "Sarah Chen", "entity_type": "Person", "description": "VP"},
        {"entity_name": "Q3 Deal", "entity_type": "Order", "value": "$8,000"},
    ]
    relations = [
        {"src_id": "Sarah Chen", "tgt_id": "Q3 Deal", "keywords": "approved",
         "description": "signed off"},
    ]
    rep = ExtractionValidator(onto).validate(entities=entities, relations=relations)
    assert rep.ok
    # top-level non-reserved keys became properties → coerced
    order = [i for i in rep.items if i.ref.startswith("Order:")][0]
    assert order.coerced["value"] == 8000.0
    summary = rep.summary()
    assert summary["total"] == 3 and summary["conforming"] == 3


@pytest.mark.offline
def test_summary_counts_and_unknowns(onto):
    rep = ExtractionValidator(onto).validate(
        entities=[
            ExtractedEntity("Sarah", "Person"),
            ExtractedEntity("HQ", "Building"),       # unknown
            ExtractedEntity("Bad", "Order", {"value": "xyz"}),  # invalid
        ],
    )
    s = rep.summary()
    assert s["total"] == 3
    assert s["by_status"][CONFORMS] == 1
    assert s["by_status"][UNKNOWN_TYPE] == 1
    assert s["by_status"][INVALID] == 1
    assert s["unknown_types"] == ["Building"]
    assert s["ok"] is False        # the INVALID one fails even in open-world


# ── the spelling extraction actually produces (W46, P15.1) ───────────────────


@pytest.mark.offline
def test_a_lower_case_entity_type_conforms(onto):
    """**Closed-world mode was rejecting every node the extractor got right.**

    Extraction produced `order` while the ontology declared `Order`. The answer
    surface was fixed to compare normalised; this validator was not, so
    governance and the answer surface disagreed about whether the same node had
    a known type.
    """
    v = ExtractionValidator(onto)
    rep = v.validate(entities=[ExtractedEntity("Big Deal", "order", {"value": "$25,000"})])
    assert rep.items[0].status == CONFORMS, rep.items[0].errors


@pytest.mark.offline
def test_a_relation_with_lower_case_endpoints_conforms(onto):
    """The endpoints, not just the link name.

    This is the assertion that was missing: reverting `_side_ok` to an exact
    comparison left every test green, because nothing fed this path a spelling
    the extractor would actually produce. A control that cannot fail is not a
    control, and this file had one.
    """
    v = ExtractionValidator(onto)
    rep = v.validate(relations=[ExtractedRelation("approved", "Ada", "Big Deal",
                                                  source_type="person", target_type="order")])
    assert rep.items[0].status == CONFORMS, rep.items[0].errors


@pytest.mark.offline
def test_control_a_wrong_endpoint_is_still_refused_however_it_is_spelled(onto):
    """Normalising must not turn domain/range checking into a rubber stamp:
    `order → person` is backwards and must stay refused in either casing."""
    v = ExtractionValidator(onto)
    rep = v.validate(relations=[ExtractedRelation("approved", "Big Deal", "Ada",
                                                  source_type="order", target_type="person")])
    assert rep.items[0].status == INVALID
