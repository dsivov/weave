"""Two people editing one artifact: the second gets 409, never a silent overwrite (R31).

The failure this closes is quiet and total. Two people open the same artifact at
v3. Both edit. Both apply. Without a version check the second write wins, the
first person's change is gone, and *nobody is told* — the losing author already
saw a success message and has no reason to look again. There is no error, no
audit line, and no way to notice until someone asks why a change was undone.

**A silent overwrite fails the M3 gate**, so it is asserted directly rather than
implied by the 409: the test reads the artifact back and requires the first
author's content to still be there.

The 409 carries a **merge view** — base, theirs, mine. A bare rejection leaves
someone holding an edit they cannot land, which is how "just force it" becomes
the habit that makes the check pointless.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave_core.governance.ontology import InMemoryOntologyStore, OntologyService
from weave_core.studio.schema import ArtifactDiff
from weave_core.studio.service import DiffEngine, StaleWrite
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"

V1 = {
    "name": "vocabulary",
    "object_types": [{"name": "Widget", "properties": []}],
    "link_types": [],
}


def _engine():
    ontology = OntologyService(InMemoryOntologyStore(now=lambda: 1.0))
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(), ontology_service=ontology, now=lambda: 1.0
    )
    return engine, ontology


def _ontology(*types: str) -> dict:
    return {
        "name": "vocabulary",
        "object_types": [{"name": t, "properties": []} for t in types],
        "link_types": [],
    }


async def _apply(engine, draft: dict, *, from_version):
    """Apply a diff drafted against `from_version`."""
    diff = ArtifactDiff(
        kind="ontology",
        artifact_id="ontology",
        to_version=int(from_version or 0) + 1,
        from_version=from_version,
        delta={"before": {}, "after": draft},
        behaviour_changed=False,
        origin="authoring",
    )
    return await engine.apply(WORKSPACE, diff, approver="alice", reason="edit")


# ── the race ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_second_writer_is_refused_and_the_first_edit_survives():
    """The whole point, stated as the gate states it."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base_version = ontology.get_summary(WORKSPACE)["version"]

    # Both authors draft against the same version.
    alice_draft = _ontology("Widget", "AliceType")
    bob_draft = _ontology("Widget", "BobType")

    await _apply(engine, alice_draft, from_version=base_version)

    with pytest.raises(StaleWrite) as exc:
        await _apply(engine, bob_draft, from_version=base_version)

    # The refusal is specific about what moved.
    assert exc.value.expected == base_version
    assert exc.value.actual == base_version + 1

    # And — the assertion that actually matters — Alice's change is still there.
    names = {o["name"] for o in ontology.get_summary(WORKSPACE)["object_types"]}
    assert "AliceType" in names, "the first author's change was overwritten"
    assert "BobType" not in names, "the stale write landed anyway"


@pytest.mark.asyncio
async def test_a_write_against_the_current_version_succeeds():
    """The check must not refuse ordinary sequential editing."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)

    v = ontology.get_summary(WORKSPACE)["version"]
    await _apply(engine, _ontology("Widget", "First"), from_version=v)

    v = ontology.get_summary(WORKSPACE)["version"]
    await _apply(engine, _ontology("Widget", "First", "Second"), from_version=v)

    names = {o["name"] for o in ontology.get_summary(WORKSPACE)["object_types"]}
    assert {"First", "Second"} <= names


@pytest.mark.asyncio
async def test_creating_something_that_already_exists_is_a_conflict_too():
    """`from_version is None` means "this did not exist when I started". If it
    exists now, someone created it concurrently — which is the same lost update
    wearing different clothes."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)   # created by someone else in the meantime

    with pytest.raises(StaleWrite):
        await _apply(engine, _ontology("Mine"), from_version=None)


@pytest.mark.asyncio
async def test_a_refused_write_records_no_sign_off():
    """The check runs before the approval decision, so a refused write leaves no
    audit trail of an approval that never happened."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base = ontology.get_summary(WORKSPACE)["version"]
    await _apply(engine, _ontology("Widget", "First"), from_version=base)

    history_before = len(engine._studio.history(WORKSPACE, "ontology", "ontology"))
    with pytest.raises(StaleWrite):
        await _apply(engine, _ontology("Widget", "Stale"), from_version=base)

    assert (
        len(engine._studio.history(WORKSPACE, "ontology", "ontology"))
        == history_before
    ), "a refused write appended to the signed ledger"


# ── the merge view ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_refusal_carries_base_theirs_and_mine():
    """A bare 409 leaves someone holding an edit they cannot land, and "just
    force it" becomes the habit that makes the check pointless."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base_version = ontology.get_summary(WORKSPACE)["version"]

    await _apply(engine, _ontology("Widget", "AliceType"), from_version=base_version)

    with pytest.raises(StaleWrite) as exc:
        await _apply(engine, _ontology("Widget", "BobType"), from_version=base_version)

    merge = exc.value.merge
    assert set(merge) == {"base", "theirs", "mine"}

    theirs = {o["name"] for o in merge["theirs"]["object_types"]}
    mine = {o["name"] for o in merge["mine"]["object_types"]}
    assert "AliceType" in theirs, "'theirs' must be what is actually there now"
    assert "BobType" in mine, "'mine' must be the edit that was refused"


@pytest.mark.asyncio
async def test_an_unavailable_base_is_null_rather_than_invented():
    """An invented base would produce a merge that looks authoritative and is
    not. Null says "reconcile by hand", which is true."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    current = ontology.get_summary(WORKSPACE)["version"]

    # Drafted against a version the ledger never recorded.
    with pytest.raises(StaleWrite) as exc:
        await _apply(engine, _ontology("Mine"), from_version=current + 99)

    assert exc.value.merge["base"] is None


@pytest.mark.asyncio
async def test_the_refusal_says_what_moved_and_what_to_do():
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base = ontology.get_summary(WORKSPACE)["version"]
    await _apply(engine, _ontology("Widget", "First"), from_version=base)

    with pytest.raises(StaleWrite) as exc:
        await _apply(engine, _ontology("Widget", "Stale"), from_version=base)

    message = str(exc.value)
    assert "Someone else saved" in message
    assert "Merge and re-apply" in message

    body = exc.value.to_dict()
    assert body["expected_version"] == base and body["current_version"] == base + 1


# ── the guard is in the service, so every caller inherits it (W4) ────────────


@pytest.mark.asyncio
async def test_the_check_lives_in_the_engine_not_only_in_the_router():
    """Watch item W4: a rule enforced in an adapter protects only the callers who
    arrive through it. The wizard and anything else composing the engine writes
    through `apply` without touching HTTP, so the check has to be there."""
    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base = ontology.get_summary(WORKSPACE)["version"]
    await _apply(engine, _ontology("Widget", "First"), from_version=base)

    # No FastAPI anywhere in this test — the refusal is the engine's.
    with pytest.raises(StaleWrite):
        await _apply(engine, _ontology("Widget", "Stale"), from_version=base)


def test_a_stale_write_is_409_over_http_with_the_merge_view():
    """The status code matters: 409 says "retry after reconciling", which is
    true, where 422 would say "your request was malformed", which is not."""
    import asyncio

    from weave.server.routers.studio import create_studio_routes

    engine, ontology = _engine()
    ontology.save(WORKSPACE, V1)
    base = ontology.get_summary(WORKSPACE)["version"]
    asyncio.get_event_loop_policy().new_event_loop()

    class _Rag:
        rules_gate = object()

    app = FastAPI()
    app.include_router(
        create_studio_routes(_Rag(), engine, workspace_resolver=lambda: WORKSPACE)
    )

    def _diff(after, from_version):
        return {
            "kind": "ontology", "artifact_id": "ontology",
            "to_version": int(from_version or 0) + 1, "from_version": from_version,
            "delta": {"before": {}, "after": after},
            "behaviour_changed": False, "origin": "authoring",
        }

    with TestClient(app) as client:
        first = client.post("/studio/apply", json={
            "diff": _diff(_ontology("Widget", "AliceType"), base),
            "approver": "alice", "reason": "edit"})
        assert first.status_code == 200, first.text

        second = client.post("/studio/apply", json={
            "diff": _diff(_ontology("Widget", "BobType"), base),
            "approver": "bob", "reason": "edit"})

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["expected_version"] == base
    assert set(detail["merge"]) == {"base", "theirs", "mine"}

    names = {o["name"] for o in ontology.get_summary(WORKSPACE)["object_types"]}
    assert "AliceType" in names and "BobType" not in names
