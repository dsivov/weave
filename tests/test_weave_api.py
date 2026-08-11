"""P0/P1 — the /weave HTTP surface: the feature-flag gate, bootstrap, and the
task-coordination endpoints (create → ready → brief → atomic claim).

Offline; a fake rag backs the seed; the authenticated role is patched so the
governed claim path (RBAC 403 → lifecycle role gate → 409-on-loser) can be
exercised without the full auth stack.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave_core.governance.actions import ActionService, InMemoryActionStore
from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave_core.governance.ontology import InMemoryOntologyStore, OntologyService
from weave_core.governance.rbac import InMemoryRbacStore, RbacService
from weave_core.governance.rules import InMemoryRuleStore, RulesService
from weave.team import (
    InMemoryIntegrationStore, InMemoryWeaveTaskStore, InMemoryWeaveWorkerStore,
    WeaveCoordinator, WorkerRegistry, preset,
)
from weave.server.routers import team as weave_routes
from weave.server.routers.team import create_weave_routes


class _FakeGraph:
    def __init__(self):
        self.nodes = {}

    async def upsert_node(self, nid, data):
        self.nodes[nid] = dict(data)


class _FakeRag:
    def __init__(self):
        self.rules_gate = None
        self.chunk_entity_relation_graph = _FakeGraph()
        self.decisions = []

    async def emit_decision_trace(self, src, tgt, rt, rc, upsert=True):
        self.decisions.append((src, tgt, rt))

        class _GD:
            outcome = "PASS"
            audit = {"outcome": "PASS"}
        return _GD()

    async def find_precedents(self, query, top_k=10, min_confidence=0.0):
        return [{"decision_trace": "prior work", "score": 0.8}]


def _client(mounted=True, with_coordinator=False):
    app = FastAPI()
    rag = _FakeRag()
    svc = dict(
        ontology_service=OntologyService(InMemoryOntologyStore()),
        rules_service=RulesService(InMemoryRuleStore()),
        action_service=ActionService(InMemoryActionStore()),
        rbac_service=RbacService(InMemoryRbacStore()),
        lifecycle_service=LifecycleService(InMemoryLifecycleStore()),
    )
    coord = reg = None
    if with_coordinator:
        coord = WeaveCoordinator(
            InMemoryWeaveTaskStore(), lifecycle_service=svc["lifecycle_service"],
            rag_resolver=lambda ws: rag, integration_store=InMemoryIntegrationStore())
        reg = WorkerRegistry(InMemoryWeaveWorkerStore(), rag_resolver=lambda ws: rag)
    if mounted:
        app.include_router(create_weave_routes(
            rag, **svc, coordinator=coord, registry=reg, api_key=None,
            workspace_resolver=lambda: "proj"))
    client = TestClient(app)
    client.coord = coord            # tests seed governed tasks directly (route is RBAC-gated)
    client.registry = reg
    return client, rag, svc


@pytest.mark.offline
class TestWeaveApi:
    def test_status_bootstrap_status(self, monkeypatch):
        client, rag, _ = _client()
        s = client.get("/weave/status").json()
        assert s["enabled"] is True and s["installed"] is False
        assert s["preset"]["object_types"] == 18 and s["preset"]["actions"] == 15

        # bootstrap installs governance → supervisors only
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "architect"})
        body = client.post("/weave/bootstrap").json()
        assert body["installed"] is True and body["roles_seeded"] == 4
        assert set(rag.chunk_entity_relation_graph.nodes) == {
            "manager", "architect", "developer", "integrator"}
        assert client.get("/weave/status").json()["installed"] is True

    def test_flag_off_means_no_routes(self):
        client, _, _ = _client(mounted=False)
        assert client.get("/weave/status").status_code == 404
        assert client.post("/weave/bootstrap").status_code == 404


@pytest.mark.offline
class TestWeaveCoordinationApi:
    def _setup(self, monkeypatch, role="developer"):
        client, rag, svc = _client(with_coordinator=True)
        # governance must be installed for RBAC/lifecycle to enforce the claim
        preset.install("proj", **svc)
        # patch the authenticated principal's role (normally from the token)
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": role})
        return client

    def test_create_ready_brief_claim(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        # tasks are authored by planning (Architect); seed them directly
        client.coord.create_task("proj", "t1", title="base")
        client.coord.create_task("proj", "t2", title="needs t1", depends_on=["t1"])

        ready = [t["id"] for t in client.get("/weave/tasks/ready").json()["ready"]]
        assert ready == ["t1"]          # t2 blocked on t1

        brief = client.get("/weave/tasks/t2/brief").json()
        assert brief["depends_on"] == [{"id": "t1", "status": "pending"}]

        r = client.post("/weave/tasks/t1/claim", json={"worker": "dev-1"})
        assert r.status_code == 200 and r.json()["status"] == "in_progress"
        # the second claimer loses
        assert client.post("/weave/tasks/t1/claim", json={"worker": "dev-2"}).status_code == 409

    def test_create_task_route_is_rbac_gated(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        # a developer may not author tasks (planning is Manager/Architect)
        assert client.post("/weave/tasks", json={"id": "x"}).status_code == 403
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "architect"})
        assert client.post("/weave/tasks", json={"id": "x"}).status_code == 200

    def test_bootstrap_requires_a_supervisor(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        assert client.post("/weave/bootstrap").status_code == 403

    def test_role_gate_blocks_manager_claim(self, monkeypatch):
        client = self._setup(monkeypatch, role="manager")
        client.coord.create_task("proj", "t1")
        # manager passes RBAC (wildcard) but the lifecycle role gate refuses the claim
        assert client.post("/weave/tasks/t1/claim", json={"worker": "pm"}).status_code == 403

    def test_unknown_task_404(self, monkeypatch):
        client = self._setup(monkeypatch)
        assert client.get("/weave/tasks/nope/brief").status_code == 404
        assert client.post("/weave/tasks/nope/claim", json={"worker": "d"}).status_code in (404, 409)

    def test_record_decision(self, monkeypatch):
        client = self._setup(monkeypatch, role="architect")
        r = client.post("/weave/decisions", json={
            "src": "architect", "tgt": "RFC-1", "relation": "designs",
            "decision_trace": "chose Postgres over Mongo", "rationale": "relational + txns"})
        assert r.status_code == 200 and r.json()["outcome"] == "PASS"

    def test_record_decision_allowed_for_developer(self, monkeypatch):
        # the loop's "record the why" — developers hold RecordDecision
        client = self._setup(monkeypatch, role="developer")
        r = client.post("/weave/decisions", json={
            "src": "dev-1", "tgt": "t1", "relation": "implemented",
            "decision_trace": "built the jwt slice"})
        assert r.status_code == 200 and r.json()["outcome"] == "PASS"

    def test_wait_returns_ready_immediately(self, monkeypatch):
        client = self._setup(monkeypatch)
        client.coord.create_task("proj", "t1", title="work")
        r = client.get("/weave/tasks/wait?timeout=2").json()
        assert [t["id"] for t in r["ready"]] == ["t1"] and r["waited"] == 0.0

    def test_brief_carries_precedent(self, monkeypatch):
        client = self._setup(monkeypatch)
        client.coord.create_task("proj", "t1", title="add auth", description="jwt")
        b = client.get("/weave/tasks/t1/brief").json()
        assert b["precedent"] == [{"decision_trace": "prior work", "score": 0.8}]

    def test_publish_plan_releases_tasks(self, monkeypatch):
        client = self._setup(monkeypatch, role="architect")
        r = client.post("/weave/plan/publish", json={
            "plan_ref": "RFC-7", "plan_kind": "RFC", "summary": "auth subsystem",
            "tasks": [{"id": "t1", "title": "jwt"},
                      {"id": "t2", "title": "login", "depends_on": ["t1"]}]})
        assert r.status_code == 200
        body = r.json()
        assert body["plan_ref"] == "RFC-7" and body["tasks"] == ["t1", "t2"]
        assert body["decision"]["outcome"] == "PASS"
        # released onto the queue; t2 blocked on t1
        ready = [t["id"] for t in client.get("/weave/tasks/ready").json()["ready"]]
        assert ready == ["t1"]

    def test_publish_plan_rbac_blocks_developer(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        r = client.post("/weave/plan/publish", json={"plan_ref": "RFC-1", "tasks": []})
        assert r.status_code == 403

    def test_artifact_chain_endpoints(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        client.coord.create_task("proj", "t1", title="auth")
        client.post("/weave/tasks/t1/claim", json={"worker": "dev-1"})
        assert client.post("/weave/tasks/t1/commit",
                           json={"sha": "abc12345", "subject": "jwt"}).status_code == 200
        pr = client.post("/weave/tasks/t1/pull-request",
                         json={"branch": "feat/t1", "url": "http://pr/1"})
        assert pr.status_code == 200 and pr.json()["status"] == "review"
        # sign-off as the architect
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "architect"})
        assert client.post("/weave/tasks/t1/review",
                           json={"verdict": "approve"}).status_code == 200
        chain = client.get("/weave/tasks/t1/chain").json()
        assert [c["sha"] for c in chain["commits"]] == ["abc12345"]
        assert chain["pull_request"]["branch"] == "feat/t1"
        assert chain["reviews"][0]["verdict"] == "approve"

    def test_automated_review_pass(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        client.coord.create_task("proj", "t1", title="auth change", touches=["auth"])
        client.post("/weave/tasks/t1/claim", json={"worker": "dev-1"})
        client.post("/weave/tasks/t1/pull-request", json={"branch": "b"})
        r = client.post("/weave/tasks/t1/review/auto").json()
        assert r["verdict"] == "flag" and r["requires_architect"] is True

    def test_integration_merge_gate(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        client.coord.create_task("proj", "t1", title="auth")
        client.post("/weave/tasks/t1/claim", json={"worker": "dev-1"})
        client.post("/weave/tasks/t1/pull-request", json={"branch": "b"})
        # architect approves the review
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "architect"})
        assert client.post("/weave/tasks/t1/advance", json={"to": "approved"}).status_code == 200
        # integrator declares the env and gates the merge
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "integrator"})
        assert client.post("/weave/environment", json={"id": "env", "name": "staging"}).status_code == 200
        # no green run yet → merge blocked
        assert client.post("/weave/tasks/t1/promote", json={"environment": "env"}).status_code == 409
        client.post("/weave/integration/run",
                    json={"environment": "env", "tasks": ["t1"], "passed": True, "kind": "e2e"})
        r = client.post("/weave/tasks/t1/promote", json={"environment": "env"})
        assert r.status_code == 200 and r.json()["status"] == "done"

    def test_deploy_and_run_require_integrator_grant(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        assert client.post("/weave/integration/deploy",
                           json={"environment": "env", "tasks": []}).status_code == 403
        assert client.post("/weave/integration/run",
                           json={"environment": "env", "tasks": []}).status_code == 403
        assert client.post("/weave/tasks/t1/promote",
                           json={"environment": "env"}).status_code == 403

    def test_worker_register_heartbeat_and_control(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        # a developer registers itself into the fleet
        r = client.post("/weave/workers/register",
                        json={"worker": "dev-1", "host": "box", "goal": "build"})
        assert r.status_code == 200 and r.json()["status"] == "active"
        # it appears in the fleet
        fleet = client.get("/weave/workers").json()["workers"]
        assert [w["id"] for w in fleet] == ["dev-1"]
        # heartbeat returns the control-state
        hb = client.post("/weave/workers/dev-1/heartbeat", json={"current_task": "t1"}).json()
        assert hb["control"] == "run" and hb["current_task"] == "t1"

    def test_worker_control_is_supervisor_only(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        client.post("/weave/workers/register", json={"worker": "dev-1"})
        # a developer may not steer the fleet
        assert client.post("/weave/workers/dev-1/control",
                           json={"action": "stop"}).status_code == 403

    def test_supervisor_pause_reaches_worker(self, monkeypatch):
        # register as the worker (owner bound), steer as the architect, then the
        # worker learns it on its own next heartbeat.
        client = self._setup(monkeypatch, role="developer")
        client.post("/weave/workers/register", json={"worker": "dev-1"})
        monkeypatch.setattr(weave_routes, "get_principal",
                            lambda req: {"username": "ana", "role": "architect"})
        assert client.post("/weave/workers/dev-1/control",
                           json={"action": "pause"}).status_code == 200
        monkeypatch.setattr(weave_routes, "get_principal", lambda req: {"role": "developer"})
        assert client.post("/weave/workers/dev-1/heartbeat", json={}).json()["control"] == "pause"

    def test_worker_ownership_is_enforced(self, monkeypatch):
        client = self._setup(monkeypatch, role="developer")
        # bo registers dev-1
        monkeypatch.setattr(weave_routes, "get_principal",
                            lambda req: {"username": "bo", "role": "developer"})
        assert client.post("/weave/workers/register", json={"worker": "dev-1"}).status_code == 200
        # cy cannot hijack bo's worker id
        monkeypatch.setattr(weave_routes, "get_principal",
                            lambda req: {"username": "cy", "role": "developer"})
        assert client.post("/weave/workers/register", json={"worker": "dev-1"}).status_code == 403
        assert client.post("/weave/workers/dev-1/heartbeat", json={}).status_code == 403


@pytest.mark.offline
class TestWeaveRoleKits:
    def test_role_directory_and_kit(self):
        client, _, _ = _client()
        roles = {r["role"] for r in client.get("/weave/roles").json()["roles"]}
        assert roles == {"manager", "architect", "developer", "integrator", "lead"}

        kit = client.get("/weave/kit?role=developer").json()
        assert kit["role"] == "developer"
        assert "mcp_config" in kit and kit["claude_md"].startswith("# Weave — Developer")
        assert kit["endpoints"] and kit["slash_commands"]

        assert client.get("/weave/kit?role=nope").status_code == 404
