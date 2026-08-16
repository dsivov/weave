"""The demo exercises every object type the ontology declares (P15, D-050).

**Why this test exists.** The ontology declared **18** object types and
`seed_demo.py` produced **8**; the demo workspace everyone had been reading held
**5**. `Task`, `PRD`, `RFC`, `Diagram`, `Module`, `Question`, `Worker`,
`DevHost`, `Environment` and `IntegrationRun` had never been seeded — so ten of
the types the answer surface is built on had never existed in any instance, and
every gate that read the demo exercised fewer than half the vocabulary it claims
to serve. **A type declared in the ontology and absent from every instance is a
type nobody has ever seen work.**

Adding the ten was a one-off; keeping them is what this test does. A type added
to the ontology now fails here until the seed covers it, which is the only reason
the next ten do not go the same way.

**What this proves, and what it does not.** It proves the *script* covers the
vocabulary — that for every declared type there is either a node the script
writes or a route it calls that the product source creates that type in. It does
**not** prove a node landed: that needs a live server, and it is the M15 gate,
run by hand against a fresh workspace. The distinction matters here more than
usual, because this project has now had five defects guarded by tests that proved
something true and adjacent to the claim. So it is stated rather than blurred.

**The route half is verified, not asserted.** For the nine types created
server-side it would be easy to write down "`Worker` comes from
`/weave/workers/register`" and have the test pass on the strength of the
comment. Instead each entry is checked twice: the seed must call that route, and
the named product module must actually create that `entity_type`. If either side
moves, the test fails rather than keeping a stale claim alive — which is the same
failure mode (a hand-written list nobody re-derives) that P15 exists to remove.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "seed_demo.py"
ONTOLOGY = ROOT / "weave" / "team" / "preset" / "ontology.json"


#: type -> (a fragment of the route the seed calls, the module that creates it).
#: Both halves are checked against the tree; neither is taken on trust.
VIA_ROUTE = {
    "Role":           ("/weave/bootstrap",           "weave/server/routers/workspaces.py"),
    "Commit":         ("/commit",                    "weave/team/coordinator.py"),
    "PullRequest":    ("/pull-request",              "weave/team/coordinator.py"),
    "Review":         ("/review",                    "weave/model/insights.py"),
    "Insight":        ("/weave/learnings",           "weave/model/insights.py"),
    "Environment":    ("/weave/integration/deploy",  "weave/team/coordinator.py"),
    "IntegrationRun": ("/weave/integration/run",     "weave/team/coordinator.py"),
    "Worker":         ("/weave/workers/register",    "weave/team/workers.py"),
    "DevHost":        ("/weave/hosts/register",      "weave/devhost/registry.py"),
}


def declared_types() -> set[str]:
    """The ontology's object types — the authority, read rather than copied."""
    spec = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    return {t["name"] if isinstance(t, dict) else t for t in spec["object_types"]}


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def types_written_directly() -> set[str]:
    """Every `entity_type` the script writes as a node.

    Two sources, because the script has two shapes. Literal `"entity_type":
    "Feature"` pairs come from the AST; the artifact table carries its type in a
    variable, so it is read from the table itself. Both are derived from the
    script — nothing here is a list this test maintains by hand.
    """
    found: set[str] = set()
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "entity_type"
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                found.add(value.value)
    found |= {row[1] for row in _seed_module().ARTIFACTS}
    return found


def test_the_seed_covers_every_declared_object_type():
    """18 of 18. The number this test exists to defend."""
    declared = declared_types()
    covered = types_written_directly() | set(VIA_ROUTE)
    missing = sorted(declared - covered)
    assert not missing, (
        f"{len(missing)} ontology object type(s) the demo never produces: {missing}. "
        "A type declared in the ontology and absent from every instance is a type "
        "nobody has ever seen work. Seed it in scripts/seed_demo.py, or — if the "
        "product should create it — add it to VIA_ROUTE with the route and the "
        "module that creates it."
    )


def test_the_seed_writes_nothing_the_ontology_does_not_declare():
    """The other direction, which is how a stale type survives a rename."""
    declared = declared_types()
    stray = sorted(types_written_directly() - declared)
    assert not stray, (
        f"the seed writes {stray}, which the ontology does not declare. Either the "
        "ontology lost a type or the seed kept one past its rename."
    )


@pytest.mark.parametrize("etype", sorted(VIA_ROUTE))
def test_each_route_backed_type_is_verified_on_both_sides(etype):
    """The seed calls that route, and that module creates that type.

    Written down once, checked against the tree every run. A comment claiming
    `Worker` comes from `/weave/workers/register` would pass forever; this fails
    the moment either half moves.
    """
    route, module = VIA_ROUTE[etype]
    seed_text = SEED.read_text(encoding="utf-8")
    assert route in seed_text, (
        f"{etype} is claimed to come from '{route}', but seed_demo.py never calls it — "
        "so nothing in the demo creates that type."
    )
    source = (ROOT / module).read_text(encoding="utf-8")
    assert re.search(rf'"entity_type":\s*"{re.escape(etype)}"', source), (
        f"{module} no longer creates an '{etype}' node, so the demo's coverage of "
        f"that type is a stale claim. Find where it moved and update VIA_ROUTE."
    )


def test_every_seeded_locator_points_at_a_file_that_exists():
    """The script's opening claim, asserted instead of trusted.

    `seed_demo.py` opens *"Every locator in here resolves to a file that actually
    exists in this repository."* **That sentence was false** — not because the
    paths were wrong, but because `/graph/entity/create` silently discarded the
    locator fields (W44), so the nodes carried none at all. The paths themselves
    are checkable here and cheap to keep honest.
    """
    mod = _seed_module()
    paths = (
        [row[2] for row in mod.ARTIFACTS]
        + [row[1] for row in mod.MODULES]
        + [row[1] for row in mod.QUESTIONS]
        + [row[1] for row in mod.ADRS]
    )
    dangling = sorted({p for p in paths if not (ROOT / p).is_file()})
    assert not dangling, (
        f"{len(dangling)} seeded locator(s) point at nothing: {dangling}. A demo "
        "whose links are dead teaches the opposite of what this product claims."
    )


# ── negative controls ───────────────────────────────────────────────────────
#
# Each of the assertions above is only evidence if it can fail. These prove it.

def test_control_an_uncovered_type_fails(monkeypatch):
    """Add a type the seed does not write, and the coverage test must fail."""
    import sys
    module = sys.modules[__name__]
    original = declared_types          # captured before patching, or this recurses
    monkeypatch.setattr(module, "declared_types", lambda: original() | {"Postmortem"})
    with pytest.raises(AssertionError, match="Postmortem"):
        test_the_seed_covers_every_declared_object_type()


def test_control_a_route_that_is_not_called_fails(monkeypatch):
    """Point a type at a route the seed never calls, and the check must fail."""
    monkeypatch.setitem(VIA_ROUTE, "Worker",
                        ("/weave/workers/enlist", "weave/team/workers.py"))
    with pytest.raises(AssertionError, match="never calls it"):
        test_each_route_backed_type_is_verified_on_both_sides("Worker")


def test_control_a_module_that_stopped_creating_the_type_fails(monkeypatch):
    """Point a type at a module that does not create it, and the check must fail."""
    monkeypatch.setitem(VIA_ROUTE, "Worker",
                        ("/weave/workers/register", "weave/model/insights.py"))
    with pytest.raises(AssertionError, match="stale claim"):
        test_each_route_backed_type_is_verified_on_both_sides("Worker")
