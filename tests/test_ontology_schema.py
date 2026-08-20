"""Tests for the Weave-native ontology schema model (P2, step 1). Fully offline."""

from __future__ import annotations

import pytest

from weave_core.governance.ontology import (
    Cardinality,
    LinkType,
    ObjectType,
    Ontology,
    Property,
    PropertyKind,
)


# ── Property validation / coercion ───────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("kind,raw,expected", [
    (PropertyKind.STRING, 42, "42"),
    (PropertyKind.INTEGER, "7", 7),
    (PropertyKind.INTEGER, 7.9, 7),
    (PropertyKind.FLOAT, "3.5", 3.5),
    (PropertyKind.BOOLEAN, "yes", True),
    (PropertyKind.BOOLEAN, "false", False),
    (PropertyKind.DATE, "2026-09-01", "2026-09-01"),
    (PropertyKind.MONEY, "$25,000", 25000.0),
    (PropertyKind.MONEY, 8000, 8000.0),
    (PropertyKind.PERCENT, "20% discount", 0.20),
    (PropertyKind.PERCENT, 0.15, 0.15),
])
def test_property_coercion(kind, raw, expected):
    assert Property("p", kind).validate(raw) == expected


@pytest.mark.offline
def test_property_bad_values_raise():
    with pytest.raises(ValueError):
        Property("d", PropertyKind.DATE).validate("not-a-date")
    with pytest.raises(ValueError):
        Property("b", PropertyKind.BOOLEAN).validate("maybe")
    with pytest.raises(ValueError):
        Property("m", PropertyKind.MONEY).validate("no number")


@pytest.mark.offline
def test_enum_property():
    p = Property("status", PropertyKind.ENUM, enum_values=["open", "closed"])
    assert p.validate("open") == "open"
    with pytest.raises(ValueError):
        p.validate("pending")


@pytest.mark.offline
def test_enum_requires_values():
    with pytest.raises(ValueError):
        Property("s", PropertyKind.ENUM)


@pytest.mark.offline
def test_numeric_bounds():
    p = Property("pct", PropertyKind.PERCENT, minimum=0.0, maximum=1.0)
    assert p.validate("50%") == 0.5
    with pytest.raises(ValueError):
        p.validate("150%")


@pytest.mark.offline
def test_bounds_only_on_numeric():
    with pytest.raises(ValueError):
        Property("s", PropertyKind.STRING, minimum=0)


# ── ObjectType validation ────────────────────────────────────────────────────


@pytest.fixture
def order_type():
    return (ObjectType("Order", description="a purchase order")
            .add(Property("value", PropertyKind.MONEY, required=True))
            .add(Property("status", PropertyKind.ENUM, enum_values=["pending", "approved"])))


@pytest.mark.offline
def test_object_validate_ok_and_coerces(order_type):
    r = order_type.validate({"value": "$25,000", "status": "approved"})
    assert r.ok and not r.errors
    assert r.coerced == {"value": 25000.0, "status": "approved"}


@pytest.mark.offline
def test_object_missing_required(order_type):
    r = order_type.validate({"status": "pending"})
    assert not r.ok
    assert any("missing required" in e and "value" in e for e in r.errors)


@pytest.mark.offline
def test_object_unknown_property_is_warning(order_type):
    r = order_type.validate({"value": 10, "note": "hi"})
    assert r.ok                                   # unknown props don't fail
    assert any("unknown property 'note'" in w for w in r.warnings)


@pytest.mark.offline
def test_object_bad_enum_fails(order_type):
    r = order_type.validate({"value": 10, "status": "nope"})
    assert not r.ok and any("status" in e for e in r.errors)


# ── LinkType / cardinality ───────────────────────────────────────────────────


@pytest.mark.offline
def test_cardinality_uniqueness_flags():
    assert Cardinality.ONE_TO_MANY.target_is_unique is False
    assert Cardinality.ONE_TO_MANY.source_is_unique is True
    assert Cardinality.MANY_TO_ONE.target_is_unique is True
    assert Cardinality.MANY_TO_MANY.source_is_unique is False


@pytest.mark.offline
def test_link_allows_typed_endpoints():
    link = LinkType("PLACES", source_types=["Customer"], target_types=["Order"],
                    cardinality=Cardinality.ONE_TO_MANY)
    assert link.allows("Customer", "Order")
    assert not link.allows("Order", "Customer")   # directed → reversed not allowed
    assert link.directed is True


@pytest.mark.offline
def test_undirected_link_accepts_reversed():
    link = LinkType("RELATED", source_types=["A"], target_types=["B"], directed=False)
    assert link.allows("A", "B")
    assert link.allows("B", "A")


@pytest.mark.offline
def test_link_validate_rejects_wrong_types():
    link = LinkType("PLACES", source_types=["Customer"], target_types=["Order"])
    r = link.validate("Vendor", "Order")
    assert not r.ok and any("not allowed" in e for e in r.errors)


@pytest.mark.offline
def test_link_validates_its_properties():
    link = LinkType("APPROVES", properties={
        "amount": Property("amount", PropertyKind.MONEY)
    })
    r = link.validate("Person", "Order", {"amount": "$1,000"})
    assert r.ok and r.coerced["amount"] == 1000.0


# ── Ontology container ───────────────────────────────────────────────────────


@pytest.fixture
def ecommerce():
    onto = Ontology(name="shop")
    onto.define_object(ObjectType("Customer").add(Property("name", PropertyKind.STRING)))
    onto.define_object(ObjectType("Order").add(Property("value", PropertyKind.MONEY, required=True)))
    onto.define_link(LinkType("PLACES", ["Customer"], ["Order"], Cardinality.ONE_TO_MANY))
    return onto


@pytest.mark.offline
def test_ontology_validate_entity_and_relation(ecommerce):
    assert ecommerce.validate_entity("Order", {"value": "$50"}).ok
    assert ecommerce.validate_relation("PLACES", "Customer", "Order").ok
    assert not ecommerce.validate_relation("PLACES", "Order", "Customer").ok


@pytest.mark.offline
def test_ontology_unknown_type(ecommerce):
    assert not ecommerce.validate_entity("Widget", {}).ok
    assert not ecommerce.validate_relation("BUYS", "Customer", "Order").ok


@pytest.mark.offline
def test_ontology_lint_catches_dangling_link():
    onto = Ontology()
    onto.define_object(ObjectType("Customer"))
    onto.define_link(LinkType("PLACES", ["Customer"], ["Order"]))  # Order undefined
    problems = onto.lint()
    assert any("undefined object type 'Order'" in p for p in problems)


@pytest.mark.offline
def test_ontology_names(ecommerce):
    assert ecommerce.object_type_names() == ["Customer", "Order"]
    assert ecommerce.link_type_names() == ["PLACES"]


@pytest.mark.offline
def test_ontology_roundtrip(ecommerce):
    restored = Ontology.from_dict(ecommerce.to_dict())
    assert restored.object_type_names() == ecommerce.object_type_names()
    assert restored.link_type_names() == ecommerce.link_type_names()
    # typed property + cardinality survive the round trip
    link = restored.link_types["PLACES"]
    assert link.cardinality is Cardinality.ONE_TO_MANY
    assert restored.object_types["Order"].properties["value"].kind is PropertyKind.MONEY
    assert restored.validate_entity("Order", {"value": "$99"}).coerced == {"value": 99.0}


# ─────────────────────────────────────────────────────────────────────────────
# Type lookup compares as the rest of the system compares (W46, P15.1)
# ─────────────────────────────────────────────────────────────────────────────


def _one_type_ontology():
    from weave_core.governance.ontology.schema import Ontology
    return Ontology.from_dict({
        "name": "t", "version": 1,
        "object_types": [{"name": "ChangeRequest", "properties": [
            {"name": "title", "kind": "string"}]}],
        "link_types": [{"name": "DependsOn", "source_types": ["ChangeRequest"],
                        "target_types": ["ChangeRequest"], "cardinality": "N:M",
                        "properties": []}],
    })


def test_an_ontology_type_resolves_however_it_is_spelled():
    """**The third place one defect reached.**

    Extraction produced `changerequest` while the ontology declared
    `ChangeRequest`. `weave/model/answers.py` was fixed to compare normalised and
    this was not — so in **closed-world** mode the garbage filter went on
    rejecting every node the extractor had correctly typed. The answer surface
    could see the node and governance could not.
    """
    onto = _one_type_ontology()
    for spelling in ("ChangeRequest", "changerequest", "CHANGEREQUEST",
                     "change request", "Change Request"):
        assert onto.has_object(spelling), f"{spelling!r} did not resolve"


def test_validation_accepts_the_spelling_extraction_produces():
    """`has_object` and `validate_entity` must agree — a type that resolves and
    then fails validation is the same gap moved one call deeper."""
    onto = _one_type_ontology()
    assert onto.validate_entity("changerequest", {"title": "x"}).ok


def test_links_resolve_the_same_way():
    """Links carry the same risk and had the same exact match."""
    onto = _one_type_ontology()
    assert onto.has_link("dependson")
    assert onto.validate_relation("dependson", "changerequest", "ChangeRequest").ok


def test_a_type_declared_later_still_resolves():
    """**Resolution is not cached, and this is why.**

    `define_object` may be called after construction. An index built once would
    miss it — the exact staleness this phase has already removed twice (the
    entity-type resolver, and the garbage filter's cached schema).
    """
    onto = _one_type_ontology()
    from weave_core.governance.ontology.schema import ObjectType
    onto.define_object(ObjectType(name="Postmortem", properties=[]))
    assert onto.has_object("postmortem")


# -- negative controls -------------------------------------------------------

def test_control_an_undeclared_type_still_does_not_resolve():
    """Normalising must not turn the check into a rubber stamp — which is the
    obvious way to 'fix' this and would delete the guard instead."""
    onto = _one_type_ontology()
    for absent in ("Postmortem", "postmortem", "Feature", "", "Change_Request_2"):
        assert not onto.has_object(absent), f"{absent!r} resolved and should not have"
    assert not onto.validate_entity("postmortem", {}).ok


def test_control_a_near_miss_does_not_resolve():
    """`normalize_type` folds case and spaces. It must not fold anything else —
    a resolver that matched substrings would accept `Change` for
    `ChangeRequest`."""
    onto = _one_type_ontology()
    for near in ("Change", "Request", "ChangeRequests", "ChangeRequestX"):
        assert not onto.has_object(near), f"{near!r} resolved and should not have"
