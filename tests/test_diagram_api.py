"""P6 — the /diagrams HTTP surface: the shared, server-side diagram set.

Saving runs the governed Studio gesture (assess → sign → apply → ledger), so
these tests also pin the status codes a UI or an agent session depends on: 422
when a structural change arrives without a sign-off, 400 for broken mermaid.

Offline; a fake rag records the sign-off decision.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave_core.studio.diagrams import InMemoryDiagramStore
from weave_core.studio import DiffEngine, InMemoryStudioStore


def _signed_in(app, user="tester", role="architect"):
    """Give the app an authenticated principal.

    Signing an artifact — including **removing** one (W12) — records who did it,
    so these endpoints 401 without an identity. The tests exercise the artifact
    contract, so they sign in.
    """
    @app.middleware("http")
    async def _principal(request, call_next):
        request.state.token_info = {"sub": user, "username": user, "role": role}
        return await call_next(request)

    return app


BASE = "flowchart LR\n  a[Architect] -->|publishes| q[Queue]\n  q --> d[Developer]\n"
RELABELLED = "flowchart LR\n  a[Chief Architect] -->|signs| q[Work Queue]\n  q --> d[Dev]\n"
GROWN = BASE + "  d --> a\n"


class _FakeRag:
    def __init__(self):
        self.rules_gate = None
        self.decisions = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.decisions.append({"tgt": tgt, "by": rc.approved_by, "why": rc.decision_trace})

        class _GD:
            audit = {"outcome": "PASS"}
        return _GD()


def _client(llm=None):
    from weave.server.routers.diagrams import create_diagram_routes

    rag = _FakeRag()
    store = InMemoryDiagramStore()
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        diagram_store=store,
        rag_resolver=lambda ws: rag,
        llm_resolver=(lambda ws: llm) if llm else None,
        now=lambda: 1_800_000_000.0,
    )
    app = FastAPI()

    # These routes derive the signer from the authenticated identity (A6, D-038),
    # so the request has to *have* one. Before D-038 they took the approver from
    # the body and this fixture never needed to authenticate — which is precisely
    # the defect: an unauthenticated caller could sign as anybody.
    @app.middleware("http")
    async def _authenticated(request, call_next):
        request.state.token_info = {"sub": "alice", "role": "manager"}
        return await call_next(request)
    app.include_router(create_diagram_routes(
        rag, engine, store, api_key=None, workspace_resolver=lambda: "w"))
    return TestClient(_signed_in(app)), rag, store


def _save(client, **kw):
    body = {"id": "arch", "source": BASE}
    body.update(kw)
    return client.post("/diagrams", json=body)


@pytest.mark.offline
class TestDiagramApi:
    def test_save_list_get_and_export(self):
        client, rag, _ = _client()
        r = _save(client, title="Architecture", depicts=["CR-1"], tags=["weave"],
                  reason="initial architecture")
        assert r.status_code == 200, r.text
        assert r.json()["version"] == 1
        # `alice` — the authenticated identity, not a name in the body (D-038).
        # This used to assert whatever the request supplied.
        assert r.json()["sign_off"]["approver"] == "alice"
        assert rag.decisions[-1]["tgt"] == "diagram:arch"      # the save is audited

        listed = client.get("/diagrams").json()["diagrams"]
        assert listed == [{"id": "arch", "title": "Architecture", "description": "",
                           "type": "flowchart", "version": 1,
                           "depicts": ["CR-1"], "tags": ["weave"]}]

        got = client.get("/diagrams/arch").json()
        assert got["source"] == BASE and got["type"] == "flowchart"

        assert client.get("/diagrams/arch/export").text == BASE
        md = client.get("/diagrams/arch/export", params={"format": "md"}).text
        assert md.startswith("# Architecture") and "```mermaid" in md

    def test_relabelling_saves_without_a_signoff_but_a_redrawn_arrow_does_not(self):
        client, _, _ = _client()
        _save(client, reason="initial")

        cosmetic = _save(client, source=RELABELLED)
        assert cosmetic.status_code == 200
        assert cosmetic.json()["behaviour_changed"] is False
        assert cosmetic.json()["sign_off"]["approver"] == "alice"

        structural = _save(client, source=GROWN)
        assert structural.status_code == 422
        assert "sign-off" in structural.json()["detail"]

        signed = _save(client, source=GROWN, reason="added feedback loop")
        assert signed.status_code == 200 and signed.json()["version"] == 3

    def test_broken_mermaid_is_rejected_and_nothing_is_stored(self):
        client, _, store = _client()
        r = _save(client, source="just prose", reason="oops")
        assert r.status_code == 400
        assert "mermaid diagram type" in r.json()["detail"]
        assert store.get("w", "arch") is None

    def test_unsafe_source_is_rejected(self):
        client, _, store = _client()
        r = _save(client, source="flowchart LR\n a --> b\n click a \"javascript:x()\"\n",
                  approver="Ana", reason="nope")
        assert r.status_code == 400
        assert "javascript:" in r.json()["detail"]
        assert store.get("w", "arch") is None

    def test_save_needs_source_or_spec(self):
        client, _, _ = _client()
        r = client.post("/diagrams", json={"id": "arch"})
        assert r.status_code == 400
        assert "source" in r.json()["detail"] and "spec" in r.json()["detail"]

    def test_versions_history_and_pinned_read(self):
        client, _, _ = _client()
        _save(client, reason="initial")
        _save(client, source=GROWN, reason="added feedback loop")

        history = client.get("/diagrams/arch/versions").json()["history"]
        assert [h["version"] for h in history] == [1, 2]
        assert [h["sign_off"]["reason"] for h in history] == ["initial", "added feedback loop"]

        assert client.get("/diagrams/arch", params={"version": 1}).json()["source"] == BASE
        assert client.get("/diagrams/arch").json()["source"] == GROWN

    def test_filter_by_depicts(self):
        client, _, _ = _client()
        _save(client, id="a1", depicts=["CR-7"], approver="Ana", reason="x")
        _save(client, id="a2", depicts=["CR-9"], approver="Ana", reason="x")
        found = client.get("/diagrams", params={"depicts": "CR-7"}).json()["diagrams"]
        assert [d["id"] for d in found] == ["a1"]

    def test_missing_diagram_is_404(self):
        client, _, _ = _client()
        assert client.get("/diagrams/nope").status_code == 404
        assert client.get("/diagrams/nope/export").status_code == 404
        assert client.delete("/diagrams/nope").status_code == 404
        _save(client, approver="Ana", reason="x")
        assert client.get("/diagrams/arch", params={"version": 9}).status_code == 404

    def test_delete_removes_it_from_the_shared_set(self):
        client, _, _ = _client()
        _save(client, approver="Ana", reason="x")
        assert client.delete("/diagrams/arch").json()["status"] == "deleted"
        assert client.get("/diagrams").json()["diagrams"] == []

    def test_save_from_a_natural_language_spec(self):
        async def llm(prompt, system_prompt=None, **kwargs):
            return ('{"diagram": {"source": "flowchart LR\\n  a[A] --> b[B]", "title": "AI"},'
                    ' "explanation": "drew it"}')

        client, _, store = _client(llm=llm)
        r = client.post("/diagrams", json={
            "id": "arch", "spec": "draw A going to B",
            "approver": "Ana", "reason": "drafted from a description"})
        assert r.status_code == 200, r.text
        assert "flowchart" in store.get("w", "arch").source
