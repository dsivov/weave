"""Tests for the NL→DSL rule author (wiring step 6) and /rules/generate. Offline.

Uses a scripted fake LLM (returns canned JSON) and a deterministic similarity
backend, so the full draft→validate→repair→dry-run loop runs without a model
or a real LLM.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave_core.governance.rules import InMemoryRuleStore, RulesService, RuleAuthor
from weave.server.routers.rules import create_rules_routes


# D-033: these endpoints now sign into the ledger rather than calling
# `service.save()`, so a router built without a Studio engine refuses with 503 —
# deliberately, because falling back to the direct write is how a removed second
# path returns. The tests therefore construct a real engine over the same
# service, which is also what the server does.
def _signed_in(app, user="tester", role="admin"):
    """Give the app an authenticated principal.

    D-033 made governance changes require one: an unattributed policy change
    makes "who took away my access" unanswerable, so the endpoint 401s without
    an identity. These tests exercise the *editing* contract, so they sign in.
    """
    @app.middleware("http")
    async def _principal(request, call_next):
        request.state.token_info = {"sub": user, "username": user, "role": role}
        return await call_next(request)

    return app


def _ledger(service, kind):
    from weave_core.studio.service import DiffEngine
    from weave_core.studio.store import InMemoryStudioStore

    return DiffEngine(
        studio_store=InMemoryStudioStore(),
        **{f"{kind}_service": service},
        now=lambda: 1.0,
    )



class FakeBackend:
    model_id = "fake/deterministic"
    dim = 4

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            t = t.lower()
            if any(k in t for k in ("approv", "authoriz", "grant", "go ahead")):
                out[i, 0] = 1.0
            elif any(k in t for k in ("den", "reject")):
                out[i, 1] = 1.0
            else:
                out[i, 2] = 1.0
            out[i, 3] = 0.01
        return out


GOOD_PAYLOAD = {
    "dsl": (
        'rule "large discount needs finance review"  priority 10\n'
        "when\n"
        '    sim(relation_type, "APPROVAL") > 0.4\n'
        "    and percent > 0.15\n"
        '    and approved_via == "slack"\n'
        "then\n"
        '    flag("Discount >15% approved over Slack — route to Finance for review")\n'
        "end\n"
    ),
    "concepts": {"APPROVAL": ["approved", "authorized", "granted approval", "go ahead"]},
    "fixtures": [
        {"name": "slack 20%", "expect": "FLAG",
         "decision": {"src": "S", "tgt": "M", "relation_type": "GRANTED_APPROVAL",
                      "quantitative_data": "20% discount", "approved_via": "slack"}},
        {"name": "jira 20%", "expect": "PASS",
         "decision": {"src": "a", "tgt": "b", "relation_type": "GRANTED_APPROVAL",
                      "quantitative_data": "20% discount", "approved_via": "jira"}},
    ],
    "explanation": "Flags large discounts approved over Slack.",
}

# Same intent but the condition is on the `when` line → fails the load-time lint.
BAD_PAYLOAD = {
    "dsl": 'rule "x"\nwhen sim(relation_type, "APPROVAL") > 0.4\nthen\n    flag("y")\nend\n',
    "concepts": {"APPROVAL": ["approved"]},
    "fixtures": [],
    "explanation": "broken formatting",
}


def _scripted_llm(*payloads, fence=False):
    """An async fake LLM that returns the given payloads (JSON) in sequence."""
    calls = {"n": 0}

    async def llm(prompt, system_prompt=None, **kwargs):
        p = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        body = json.dumps(p)
        return f"```json\n{body}\n```" if fence else body

    llm.calls = calls
    return llm


# ── agent unit ───────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_valid_first_try():
    author = RuleAuthor(_scripted_llm(GOOD_PAYLOAD), gate_backend=FakeBackend())
    res = await author.generate("Flag discounts over 15% approved on Slack.")
    assert res.valid and res.attempts == 1
    assert "large discount" in res.dsl
    assert res.concepts["APPROVAL"]
    # dry-run matched both fixtures' expectations
    assert [d["ok"] for d in res.dry_run] == [True, True]
    assert {d["outcome"] for d in res.dry_run} == {"FLAG", "PASS"}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_handles_code_fence():
    author = RuleAuthor(_scripted_llm(GOOD_PAYLOAD, fence=True), gate_backend=FakeBackend())
    res = await author.generate("…")
    assert res.valid


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_repairs_after_invalid_draft():
    llm = _scripted_llm(BAD_PAYLOAD, GOOD_PAYLOAD)  # first bad, then good
    author = RuleAuthor(llm, gate_backend=FakeBackend())
    res = await author.generate("…", max_repairs=1)
    assert res.valid and res.attempts == 2
    assert len(res.errors) == 1            # the first failure is recorded
    assert "empty condition" in res.errors[0]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_gives_up_after_repairs_exhausted():
    author = RuleAuthor(_scripted_llm(BAD_PAYLOAD), gate_backend=FakeBackend())
    res = await author.generate("…", max_repairs=1)
    assert res.valid is False
    assert res.attempts == 2 and len(res.errors) == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_handles_non_json():
    async def llm(prompt, system_prompt=None, **kwargs):
        return "Sure! Here are some rules: ..."   # no JSON object
    res = await RuleAuthor(llm, gate_backend=FakeBackend()).generate("…", max_repairs=0)
    assert res.valid is False
    assert "JSON" in res.errors[0]


# ── /rules/generate endpoint ─────────────────────────────────────────────────


class FakeRag:
    def __init__(self, llm):
        self.rules_gate = None
        self.llm_model_func = llm


@pytest.mark.offline
def test_generate_endpoint_review_then_save():
    svc = RulesService(InMemoryRuleStore(now=lambda: 1.0), gate_backend=FakeBackend())
    rag = FakeRag(_scripted_llm(GOOD_PAYLOAD))
    app = FastAPI()
    app.include_router(
        create_rules_routes(rag, svc, studio_engine=_ledger(svc, 'rules'), api_key=None, workspace_resolver=lambda: "acme")
    )
    client = TestClient(_signed_in(app))

    # review-only (default): valid draft returned, nothing persisted
    r = client.post("/rules/generate", json={"policy": "Flag big Slack discounts."})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["saved"] is False
    assert body["dry_run"][0]["ok"] is True
    assert svc.get_summary("acme")["exists"] is False     # not saved
    assert rag.rules_gate is None

    # apply: save=true persists and attaches the gate
    rag.llm_model_func = _scripted_llm(GOOD_PAYLOAD)       # fresh scripted llm
    r = client.post("/rules/generate", json={"policy": "Flag big Slack discounts.", "save": True})
    assert r.json()["saved"] is True
    assert svc.get_summary("acme")["exists"] is True
    assert rag.rules_gate is not None


@pytest.mark.offline
def test_generate_endpoint_503_without_llm():
    svc = RulesService(InMemoryRuleStore(), gate_backend=FakeBackend())

    class RagNoLLM:
        rules_gate = None                                  # Weave-capable but no llm

    app = FastAPI()
    app.include_router(
        create_rules_routes(RagNoLLM(), svc, studio_engine=_ledger(svc, 'rules'), api_key=None, workspace_resolver=lambda: "acme")
    )
    assert TestClient(app).post("/rules/generate", json={"policy": "x"}).status_code == 503
