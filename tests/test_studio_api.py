"""P3 — the /studio HTTP surface: propose → apply → history → revert, and the
server-side re-assess that stops a client from skipping sign-off.

Offline; a fake rag records the sign-off decision, real rules service over an
in-memory store.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave_core.governance.rules import InMemoryRuleStore, RulesService
from weave_core.studio import DiffEngine, InMemoryStudioStore


class _FakeRag:
    def __init__(self):
        self.rules_gate = None
        self.decisions = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.decisions.append({"tgt": tgt, "by": rc.approved_by, "why": rc.decision_trace})

        class _GD:
            audit = {"outcome": "PASS"}
        return _GD()


def _rule_draft(threshold: str, reason: str, name="cap"):
    return {"dsl": f'rule "{name}"\nwhen\n    percent > {threshold}\nthen\n    flag("{reason}")\nend\n',
            "concepts": {}, "enabled": True, "fixtures": []}


def _client():
    from weave.server.routers.studio import create_studio_routes

    rag = _FakeRag()
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        rules_service=RulesService(InMemoryRuleStore()),
        rag_resolver=lambda ws: rag,
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
    app.include_router(create_studio_routes(
        rag, engine, api_key=None, workspace_resolver=lambda: "w"))
    return TestClient(app), rag


@pytest.mark.offline
class TestStudioApi:
    def test_propose_apply_history_revert(self):
        client, rag = _client()

        # propose v1 (first version → behavioural)
        d1 = client.post("/studio/propose", json={
            "kind": "rule", "artifact_id": "policy",
            "draft": _rule_draft("0.20", "exceeds")}).json()["diff"]
        assert d1["behaviour_changed"] is True

        # apply with no reason → 422 (sign-off required). The approver is no
        # longer part of this: it comes from the token (D-038).
        assert client.post("/studio/apply", json={"diff": d1}).status_code == 422
        r1 = client.post("/studio/apply", json={
            "diff": d1, "reason": "initial policy"})
        assert r1.status_code == 200 and r1.json()["version"] == 1
        assert rag.decisions[-1]["tgt"] == "rule:policy"

        # cosmetic change → lightweight (no approver needed)
        d2 = client.post("/studio/propose", json={
            "kind": "rule", "artifact_id": "policy",
            "draft": _rule_draft("0.20", "over the cap", name="ceiling")}).json()["diff"]
        assert d2["behaviour_changed"] is False
        r2 = client.post("/studio/apply", json={"diff": d2})
        # `alice` — the authenticated identity, not a name in the body (D-038).
        # This used to assert whatever the request supplied.
        assert r2.status_code == 200 and r2.json()["sign_off"]["approver"] == "alice"

        # history shows both versions
        hist = client.get("/studio/history/rule/policy").json()["history"]
        assert [h["version"] for h in hist] == [1, 2]

        # revert to v1 → forward-applied as v3
        rv = client.post("/studio/revert", json={
            "kind": "rule", "artifact_id": "policy", "to_version": 1,
            "reason": "roll back"})
        assert rv.status_code == 200 and rv.json()["version"] == 3

    def test_apply_reassesses_and_blocks_tampered_flag(self):
        client, _ = _client()
        d = client.post("/studio/propose", json={
            "kind": "rule", "artifact_id": "p",
            "draft": _rule_draft("0.20", "exceeds")}).json()["diff"]
        # client lies: behaviour_changed=False to dodge sign-off
        d["behaviour_changed"] = False
        # server re-assesses → still behavioural → 422 without a reason
        assert client.post("/studio/apply", json={"diff": d}).status_code == 422

    def test_unknown_kind_400(self):
        client, _ = _client()
        r = client.post("/studio/propose", json={
            "kind": "widget", "artifact_id": "x", "draft": {}})
        assert r.status_code == 400
