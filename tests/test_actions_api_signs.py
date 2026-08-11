"""The action catalog is signed like every other governed artifact (A8, D-033).

**Found by inverting the guard, and it had no test at all.** The M5 review's
Medium 2 said the class assertion's reach was a hand-kept filename map that a new
router would not be on. Inverting it to *offender unless annotated* immediately
produced `actions.py` — a **fifth** unsigned write path that neither the original
finding nor my filename map had named.

It went unnoticed because `create_actions_routes` had **no endpoint test
anywhere**: nothing broke when the write was unsigned, and nothing broke when I
signed it. A path with no coverage is a path where a guard is the only thing
standing up, which is exactly why the guard's default matters more than its
exceptions.

What the catalog controls is not incidental: it defines **which actions exist and
what arguments they accept**, and `routers/actions.py` is the enforcement chain
(`RBAC → lifecycle → rules gate → side effect`). Changing it changes what the
runtime will invoke.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave.server.routers.actions import create_actions_routes
from weave_core.governance.actions import ActionService, InMemoryActionStore
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "acme"

CATALOG = {
    "name": "team",
    "actions": [
        {"name": "ClaimTask", "object_type": "Task",
         "description": "Claim a task", "arguments": []},
    ],
}


class _Rag:
    """`_require_cg` only checks for a `rules_gate` attribute."""

    rules_gate = object()


def _client(*, with_engine=True, signed_in=True):
    service = ActionService(InMemoryActionStore())
    engine = (
        DiffEngine(studio_store=InMemoryStudioStore(), action_service=service,
                   now=lambda: 1.0)
        if with_engine else None
    )
    app = FastAPI()
    app.include_router(create_actions_routes(
        _Rag(), service, studio_engine=engine,
        api_key=None, workspace_resolver=lambda: WORKSPACE))

    if signed_in:
        @app.middleware("http")
        async def _principal(request, call_next):
            request.state.token_info = {"sub": "arch", "username": "arch",
                                        "role": "architect"}
            return await call_next(request)

    return TestClient(app), service, engine


# ── the write is signed ──────────────────────────────────────────────────────


def test_setting_the_catalog_records_a_signed_version():
    client, service, engine = _client()

    r = client.post("/actions", json={"catalog": CATALOG})

    assert r.status_code == 200, r.text
    assert service.get_summary(WORKSPACE)["exists"] is True
    history = engine._studio.history(WORKSPACE, "action", "action")
    assert [v.version for v in history] == [1]
    assert history[0].sign_off.approver == "arch"
    assert history[0].sign_off.reason


def test_deleting_the_catalog_records_the_removal():
    """A removal is a governance change: a version saying so, with the prior
    version still there to revert to."""
    client, service, engine = _client()
    client.post("/actions", json={"catalog": CATALOG})

    r = client.delete("/actions")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["recorded"]["removed"] is True
    assert body["recorded"]["revert_to"] == 1
    assert service.get_summary(WORKSPACE)["exists"] is False

    history = engine._studio.history(WORKSPACE, "action", "action")
    assert [(v.version, v.origin) for v in history] == [(1, "authoring"), (2, "removal")]


def test_a_removal_is_structurally_distinguishable_from_an_empty_catalog():
    """The M5 review's Medium 1. An empty snapshot alone cannot say whether the
    artifact was removed or authored empty, and the two behave differently —
    so `origin` carries it, not the free-text reason."""
    client, service, engine = _client()
    client.post("/actions", json={"catalog": CATALOG})
    client.delete("/actions")

    removal = engine._studio.history(WORKSPACE, "action", "action")[-1]
    assert removal.snapshot == {}
    assert removal.origin == "removal", (
        "an empty snapshot with origin='authoring' is an authored empty catalog, "
        "which is a different thing from a removed one"
    )


# ── the refusals that keep the second path from returning ────────────────────


def test_without_a_ledger_the_write_is_refused_not_fallen_back_on():
    """503 rather than a direct save. A fallback is how a removed path returns."""
    client, service, _ = _client(with_engine=False)

    r = client.post("/actions", json={"catalog": CATALOG})

    assert r.status_code == 503
    assert "ledger" in r.json()["detail"]
    assert service.get_summary(WORKSPACE)["exists"] is False, "it wrote anyway"


def test_without_an_identity_the_write_is_refused(monkeypatch):
    """A6. An unattributed change to what the runtime may invoke is one nobody
    can be asked about."""
    client, service, _ = _client(signed_in=False)

    r = client.post("/actions", json={"catalog": CATALOG})

    assert r.status_code == 401
    assert service.get_summary(WORKSPACE)["exists"] is False


def test_deleting_nothing_records_nothing():
    """An idempotent delete should not manufacture a version recording the
    removal of something that was not there."""
    client, service, engine = _client()

    r = client.delete("/actions")

    assert r.json()["deleted"] is False
    assert engine._studio.history(WORKSPACE, "action", "action") == []
