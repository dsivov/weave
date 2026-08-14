"""Weave API — the feature-flagged surface (P0).

Mounted only when ``WEAVE_ENABLE_TEAM=true`` (so a stock server exposes zero Weave
routes). P0 ships the two endpoints that prove the foundation:

  GET  /weave/status   — is Weave on, what does its preset define, is this
                         workspace bootstrapped?
  POST /weave/bootstrap — install the governance preset into the current
                         workspace (ontology · actions · RBAC · lifecycle ·
                         rules) and seed the role identities; turns the model
                         from authored into *enforced* (403/409/422 on invoke).

Later phases add the coordination, run, and board surfaces.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import get_combined_auth_dependency, get_principal
from weave_core.utils import logger

from weave.team import playbook, preset
from weave.team.coordinator import (
    WeaveConflict, WeaveError, WeaveForbidden, WeaveNotFound,
)


class WeaveStatus(BaseModel):
    enabled: bool
    workspace: str
    governance_ready: bool
    installed: bool
    preset: dict


class CreateTaskRequest(BaseModel):
    id: str
    title: str = ""
    priority: str = "normal"
    description: str = ""
    change_request: Optional[str] = None
    touches: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    worker: str = Field(description="The claiming worker's identity (its Worker id).")


class DecisionRequest(BaseModel):
    src: str
    tgt: str
    relation: str = "decided"
    decision_trace: str = Field(description="The why — rationale / exception / approval.")
    rationale: Optional[str] = None
    policy_ref: Optional[str] = None


class PlanTaskSpec(BaseModel):
    id: str
    title: str = ""
    priority: str = "normal"
    description: str = ""
    change_request: Optional[str] = None
    touches: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)


class PublishPlanRequest(BaseModel):
    plan_ref: str = Field(description="The PRD/RFC id this plan signs off.")
    plan_kind: str = "PRD"
    summary: str = ""
    tasks: List[PlanTaskSpec] = Field(default_factory=list)


class CommitRequest(BaseModel):
    sha: str
    subject: str = ""
    touches: Optional[List[str]] = None


class PullRequestRequest(BaseModel):
    branch: str = ""
    url: str = ""
    title: str = ""


class ReviewRequest(BaseModel):
    verdict: str = Field(description="approve · flag · reject")
    notes: str = ""


class LearningRequest(BaseModel):
    insight: str = Field(description="What the loop learned — precedent for future tasks.")
    task: Optional[str] = None


class AdvanceRequest(BaseModel):
    to: str = Field(description="Target task state, e.g. approved.")


class EnvironmentRequest(BaseModel):
    id: str
    name: str = ""
    url: str = ""
    config: dict = Field(default_factory=dict)


class DeployRequest(BaseModel):
    environment: str
    tasks: List[str] = Field(default_factory=list)
    ref: str = ""


class RunIntegrationRequest(BaseModel):
    environment: str
    tasks: List[str] = Field(default_factory=list)
    kind: str = "e2e"
    passed: bool = True
    summary: str = ""


class PromoteRequest(BaseModel):
    environment: str = ""


class ProjectRequest(BaseModel):
    """Every field optional: setting the image must not reset the test command."""
    repo: Optional[str] = Field(default=None, description="Clone URL the hosts resolve.")
    base_branch: Optional[str] = None
    image: Optional[str] = None
    test_command: Optional[List[str]] = None
    setup_command: Optional[List[str]] = None
    description: Optional[str] = None


class RegisterHostRequest(BaseModel):
    host: str = Field(description="This machine's id in the fleet.")
    machine: str = ""
    capabilities: List[str] = Field(default_factory=list)
    repo: str = ""
    base_branch: str = "main"
    image: str = ""
    version: str = ""
    seat: str = Field(default="unknown",
                      description="Seat health as the host's own preflight found it.")
    seat_detail: str = ""


class HostHeartbeatRequest(BaseModel):
    workers: List[str] = Field(default_factory=list,
                               description="Worker ids actually running on this machine.")
    seat: Optional[str] = None
    seat_detail: Optional[str] = None


class ScaleHostRequest(BaseModel):
    desired_workers: int = Field(
        description="How many developers the team wants running on this machine.")


class RegisterWorkerRequest(BaseModel):
    worker: str = Field(description="The worker's own id (its Worker node id).")
    host: str = ""
    capabilities: List[str] = Field(default_factory=list)
    goal: str = ""


class HeartbeatRequest(BaseModel):
    current_task: Optional[str] = None
    step: Optional[str] = Field(
        default=None,
        description=("Which step of its loop the worker is in — diagnostic "
                     "liveness only. The governed state of a task is its "
                     "lifecycle; nothing branches on this."))


class ControlRequest(BaseModel):
    action: str = Field(description="pause · resume · stop · redirect")
    goal: str = Field(
        "", description="For `redirect`: what the worker should work on instead."
    )
    # No `actor` field, deliberately. Who performed a supervisory act comes from
    # the authenticated identity — a supervisor who could name themselves is not
    # one (A6).


class DispatchRequest(BaseModel):
    workers_per_host: int = Field(
        1, ge=0, description="How many developers each host should run."
    )
    hosts: Optional[List[str]] = Field(
        None, description="Which hosts; omit for every running host."
    )


# Supervisors may steer the fleet; a worker may not stop its peers.
SUPERVISOR_ROLES = {"manager", "architect"}


def create_weave_routes(
    rag,
    *,
    ontology_service: Any = None,
    rules_service: Any = None,
    action_service: Any = None,
    rbac_service: Any = None,
    lifecycle_service: Any = None,
    studio_engine: Any = None,
    coordinator: Any = None,
    registry: Any = None,
    host_registry: Any = None,
    project_service: Any = None,
    api_key: Optional[str] = None,
    workspace_resolver=None,
):
    """Build the /weave router. Governance services are optional so the router
    still answers ``/weave/status`` in reduced setups; bootstrap needs them."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["weave"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _installed(ws: str) -> bool:
        """True if the Weave ontology is already present in this workspace."""
        if ontology_service is None:
            return False
        try:
            onto = ontology_service.store.load(ws)
            return onto is not None and onto.name == "weave"
        except Exception:  # pragma: no cover - defensive
            return False

    @router.get("/weave/status", response_model=WeaveStatus,
                dependencies=[Depends(combined_auth)],
                summary="Weave feature status + preset overview")
    async def status():
        ws = _ws()
        governance_ready = all(s is not None for s in (
            ontology_service, rules_service, action_service, rbac_service, lifecycle_service))
        return WeaveStatus(
            enabled=True, workspace=ws,
            governance_ready=governance_ready,
            installed=_installed(ws),
            preset=preset.summary(),
        )

    @router.post("/weave/bootstrap", dependencies=[Depends(combined_auth)],
                 summary="Install the Weave governance preset into this workspace")
    async def bootstrap(request: Request):
        ws = _ws()
        # Installing rewrites the ontology, RBAC policy, lifecycle, and rules — a
        # governance-owner action. Bootstrap has no action of its own, so gate it
        # on the role directly (as control_worker does). Once a policy exists it
        # also authorises: deny anyone the installed RBAC wouldn't let re-install.
        role = _role(request)
        if role not in SUPERVISOR_ROLES:
            raise HTTPException(
                status_code=403,
                detail=f"role '{role}' may not (re)install governance (supervisors only)")
        if not hasattr(rag, "rules_gate"):
            raise HTTPException(
                status_code=503,
                detail="Installing governance requires the governance engine. Set WEAVE_ENABLE_QUADRUPLE=true — the Weave surface (WEAVE_ENABLE_TEAM) is already on, or you would not be here.")
        # Refuse rather than write unsigned. All five preset layers are
        # ledger-owned, and the rules layer in particular is enforced by the
        # gate the moment it lands — installing it with no version would make
        # A8 false for this workspace immediately, not eventually (D-032).
        if studio_engine is None:
            raise HTTPException(
                status_code=503,
                detail="governance ledger unavailable — bootstrap will not install "
                       "an unsigned preset. Start the server in Weave mode.")
        try:
            report = await preset.install(
                ws, studio_engine,
                approver=_principal_id(request) or role or "unknown",
                role=role,
                reason="onboarding: install the Weave governance preset")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"preset install failed: {e}")

        # Seed the role identities (best-effort — needs the graph store).
        seeded = 0
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is not None:
            for ent in preset.seed_entities():
                try:
                    await graph.upsert_node(ent["entity_name"], dict(ent.get("entity_data", {})))
                    seeded += 1
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning(f"Weave seed '{ent.get('entity_name')}' failed: {e}")
        report["roles_seeded"] = seeded

        # Make the gate live immediately so the next invoke enforces the policy.
        if rules_service is not None:
            try:
                rules_service.attach(rag, ws)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Weave gate attach failed for '{ws}': {e}")

        return {"workspace": ws, "installed": True, **report}

    def _role(request: Request) -> Optional[str]:
        """The authenticated principal's role — from the token, never a body
        field (D5: attribution is authenticated, not self-stamped)."""
        principal = get_principal(request)
        return principal.get("role") if principal else None

    def _principal_id(request: Request) -> Optional[str]:
        """A stable id for the authenticated principal (username, else role) —
        used to bind a worker to whoever registered it."""
        principal = get_principal(request)
        if not principal:
            return None
        return principal.get("username") or principal.get("role")

    def _server_url(request: Request) -> str:
        import os

        override = os.environ.get("WEAVE_PUBLIC_URL")
        if override:
            return override.rstrip("/")
        # Falls back to the request's Host header — spoofable behind a proxy. The
        # kit bakes this into the agent's .mcp.json, so set WEAVE_PUBLIC_URL in
        # any non-local deployment.
        logger.warning(
            "Weave kit URL derived from the request Host header; "
            "set WEAVE_PUBLIC_URL to pin the public base URL")
        return str(request.base_url).rstrip("/")

    # ── onboarding: the role directory + per-role kit (P2) ─────────────────
    @router.get("/weave/roles", dependencies=[Depends(combined_auth)],
                summary="The Weave role directory (identities, not people)")
    async def list_roles():
        return {"workspace": _ws(), "roles": playbook.roles()}

    @router.get("/weave/kit", dependencies=[Depends(combined_auth)],
                summary="A role kit: .mcp.json + CLAUDE.md loop + actions/endpoints")
    async def role_kit(role: str, request: Request):
        try:
            return playbook.role_kit(role, _ws(), _server_url(request))
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown role '{role}'; try one of {list(playbook.ROLES)}")

    # ── task coordination (pull scheduler + atomic claim) ──────────────────
    if coordinator is not None:

        @router.post("/weave/tasks", dependencies=[Depends(combined_auth)],
                     summary="Create a task (Manager/Architect)")
        async def create_task(body: CreateTaskRequest, request: Request):
            role = _role(request)
            if rbac_service is not None:
                d = rbac_service.check(_ws(), role, "invoke", "CreateTask")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            t = coordinator.create_task(
                _ws(), body.id, title=body.title, priority=body.priority,
                description=body.description, change_request=body.change_request,
                touches=body.touches, depends_on=body.depends_on, created_by=_role(request))
            return t.to_dict()

        @router.get("/weave/tasks", dependencies=[Depends(combined_auth)],
                    summary="List tasks (optional status filter)")
        async def list_tasks(status: Optional[str] = None):
            ws = _ws()
            return {"workspace": ws,
                    "tasks": [t.to_dict() for t in coordinator.list(ws, status=status)]}

        @router.get("/weave/tasks/ready", dependencies=[Depends(combined_auth)],
                    summary="The claimable ready-set (pull scheduling)")
        async def ready_tasks():
            ws = _ws()
            return {"workspace": ws, "ready": [t.to_dict() for t in coordinator.ready(ws)]}

        @router.get("/weave/tasks/wait", dependencies=[Depends(combined_auth)],
                    summary="Long-poll for claimable work (the wake mechanism)")
        async def wait_for_ready(timeout: float = 25.0):
            import asyncio
            ws = _ws()
            deadline = min(max(timeout, 0.0), 55.0)
            waited = 0.0
            while True:
                ready = [t.to_dict() for t in coordinator.ready(ws)]
                if ready or waited >= deadline:
                    return {"workspace": ws, "ready": ready, "waited": round(waited, 1)}
                await asyncio.sleep(1.0)
                waited += 1.0

        @router.get("/weave/tasks/{task_id}/brief", dependencies=[Depends(combined_auth)],
                    summary="The curated task brief a worker hands to the CLI")
        async def task_brief(task_id: str):
            try:
                return await coordinator.brief(_ws(), task_id)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        @router.post("/weave/tasks/{task_id}/claim", dependencies=[Depends(combined_auth)],
                     summary="Atomically claim a task (one winner; 409 for the loser)")
        async def claim_task(task_id: str, body: ClaimRequest, request: Request):
            ws = _ws()
            role = _role(request)
            # RBAC: may this role invoke ClaimTask at all? (403) — before the claim.
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", "ClaimTask")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            try:
                t = await coordinator.claim(ws, task_id, worker=body.worker, role=role)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveForbidden as e:
                raise HTTPException(status_code=403, detail=str(e))
            except WeaveConflict as e:
                raise HTTPException(status_code=409, detail=str(e))
            return t.to_dict()

        @router.post("/weave/decisions", dependencies=[Depends(combined_auth)],
                     summary="Record a decision on the graph (must-succeed · dual-write)")
        async def record_decision(body: DecisionRequest, request: Request):
            from weave_core.governance.rules.gate import RuleViolation
            role = _role(request)
            if rbac_service is not None:
                d = rbac_service.check(_ws(), role, "invoke", "RecordDecision")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            by = role or "system"
            try:
                return await coordinator.record_decision(
                    _ws(), src=body.src, tgt=body.tgt, relation=body.relation,
                    decision_trace=body.decision_trace, by=by,
                    rationale=body.rationale, policy_ref=body.policy_ref)
            except RuleViolation as e:
                raise HTTPException(status_code=422, detail={
                    "status": "rejected", "audit": e.decision.audit})
            except WeaveError as e:
                raise HTTPException(status_code=503, detail=str(e))

        @router.post("/weave/plan/publish", dependencies=[Depends(combined_auth)],
                     summary="Publish a plan — sign it, then release its tasks (planning gate)")
        async def publish_plan(body: PublishPlanRequest, request: Request):
            from weave_core.governance.rules.gate import RuleViolation
            ws = _ws()
            role = _role(request)
            # RBAC: may this role publish a plan at all? (403) — before the sign.
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", "PublishPlan")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            try:
                return await coordinator.publish_plan(
                    ws, plan_ref=body.plan_ref, by=(role or "system"), role=role,
                    plan_kind=body.plan_kind, summary=body.summary,
                    tasks=[t.model_dump() for t in body.tasks])
            except WeaveForbidden as e:
                raise HTTPException(status_code=403, detail=str(e))
            except RuleViolation as e:
                raise HTTPException(status_code=422, detail={
                    "status": "rejected", "audit": e.decision.audit})
            except WeaveError as e:
                raise HTTPException(status_code=503, detail=str(e))

        # ── the artifact chain (commit → PR → review → learning) ───────────
        @router.post("/weave/tasks/{task_id}/commit", dependencies=[Depends(combined_auth)],
                     summary="Record a commit against a task")
        async def record_commit(task_id: str, body: CommitRequest, request: Request):
            try:
                return await coordinator.record_commit(
                    _ws(), task_id, sha=body.sha, subject=body.subject,
                    touches=body.touches, by=_role(request) or "developer")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        @router.post("/weave/tasks/{task_id}/pull-request",
                     dependencies=[Depends(combined_auth)],
                     summary="Open the PR for a completed task (in_progress → review; cannot merge)")
        async def open_pull_request(task_id: str, body: PullRequestRequest, request: Request):
            ws = _ws()
            role = _role(request)
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", "OpenPullRequest")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            try:
                return await coordinator.open_pull_request(
                    ws, task_id, branch=body.branch, url=body.url, title=body.title,
                    by=role or "developer", role=role)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveForbidden as e:
                raise HTTPException(status_code=403, detail=str(e))
            except WeaveConflict as e:
                raise HTTPException(status_code=409, detail=str(e))

        @router.post("/weave/tasks/{task_id}/review", dependencies=[Depends(combined_auth)],
                     summary="Record a review outcome on a task's PR (two-tier)")
        async def record_review(task_id: str, body: ReviewRequest, request: Request):
            try:
                return await coordinator.record_review(
                    _ws(), task_id, verdict=body.verdict, notes=body.notes,
                    by=_role(request) or "architect")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveConflict as e:
                raise HTTPException(status_code=409, detail=str(e))

        @router.post("/weave/tasks/{task_id}/review/auto",
                     dependencies=[Depends(combined_auth)],
                     summary="Run the automated review pass (flags architecture-touching PRs)")
        async def review_pass(task_id: str, request: Request):
            try:
                return await coordinator.review_pass(_ws(), task_id)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveConflict as e:
                raise HTTPException(status_code=409, detail=str(e))

        @router.post("/weave/learnings", dependencies=[Depends(combined_auth)],
                     summary="Record an insight (must-succeed · precedent-searchable)")
        async def record_learning(body: LearningRequest, request: Request):
            from weave_core.governance.rules.gate import RuleViolation
            try:
                return await coordinator.record_learning(
                    _ws(), insight=body.insight, task_id=body.task,
                    by=_role(request) or "developer")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except RuleViolation as e:
                raise HTTPException(status_code=422, detail={
                    "status": "rejected", "audit": e.decision.audit})
            except WeaveError as e:
                raise HTTPException(status_code=503, detail=str(e))

        @router.get("/weave/tasks/{task_id}/chain", dependencies=[Depends(combined_auth)],
                    summary="Reconstruct a task's full artifact chain from Weave state")
        async def trace_chain(task_id: str):
            try:
                return coordinator.trace_chain(_ws(), task_id)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        # ── the integration environment + the merge gate (P4) ──────────────
        def _rbac(ws, role, action):
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", action)
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)

        @router.post("/weave/tasks/{task_id}/advance", dependencies=[Depends(combined_auth)],
                     summary="Advance a task's lifecycle state (e.g. review → approved)")
        async def advance_task(task_id: str, body: AdvanceRequest, request: Request):
            ws = _ws()
            role = _role(request)
            _rbac(ws, role, "AdvanceTask")
            try:
                return await coordinator.advance_task(ws, task_id, body.to, role=role)
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveForbidden as e:
                raise HTTPException(status_code=403, detail=str(e))

        @router.post("/weave/environment", dependencies=[Depends(combined_auth)],
                     summary="Declare the shared integration environment")
        async def register_environment(body: EnvironmentRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES and role != "integrator":
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not declare the environment")
            try:
                env = coordinator.register_environment(
                    _ws(), body.id, name=body.name, url=body.url, config=body.config)
            except WeaveError as e:
                raise HTTPException(status_code=503, detail=str(e))
            return env.to_dict()

        @router.get("/weave/environments", dependencies=[Depends(combined_auth)],
                    summary="The declared integration environments")
        async def list_environments():
            ws = _ws()
            return {"workspace": ws, "environments": [e.to_dict() for e in coordinator.environments(ws)]}

        @router.post("/weave/integration/deploy", dependencies=[Depends(combined_auth)],
                     summary="Deploy approved work into the shared environment (Integrator)")
        async def deploy(body: DeployRequest, request: Request):
            ws = _ws()
            _rbac(ws, _role(request), "Deploy")
            try:
                return await coordinator.deploy(
                    ws, body.environment, tasks=body.tasks, ref=body.ref,
                    by=_role(request) or "integrator")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        @router.post("/weave/integration/run", dependencies=[Depends(combined_auth)],
                     summary="Record an integration / e2e run — the merge gate's evidence")
        async def run_integration(body: RunIntegrationRequest, request: Request):
            ws = _ws()
            _rbac(ws, _role(request), "RunIntegration")
            try:
                return await coordinator.run_integration(
                    ws, body.environment, tasks=body.tasks, passed=body.passed,
                    kind=body.kind, summary=body.summary, by=_role(request) or "integrator")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        @router.post("/weave/tasks/{task_id}/promote", dependencies=[Depends(combined_auth)],
                     summary="Promote an approved task to done (Integrator merge gate)")
        async def promote(task_id: str, body: PromoteRequest, request: Request):
            ws = _ws()
            role = _role(request)
            _rbac(ws, role, "Promote")
            try:
                return await coordinator.promote(
                    ws, task_id, env_id=body.environment, role=role,
                    by=role or "integrator")
            except WeaveNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except WeaveForbidden as e:
                raise HTTPException(status_code=403, detail=str(e))
            except WeaveConflict as e:
                raise HTTPException(status_code=409, detail=str(e))
            except WeaveError as e:
                raise HTTPException(status_code=503, detail=str(e))

    # ── worker registry (fleet presence + pause/resume/stop) ───────────────
    if registry is not None:

        @router.post("/weave/workers/register", dependencies=[Depends(combined_auth)],
                     summary="Register this worker into the live fleet")
        async def register_worker(body: RegisterWorkerRequest, request: Request):
            from weave.team.workers import WorkerOwnershipError
            ws = _ws()
            role = _role(request)
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", "RegisterWorker")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            try:
                w = await registry.register(
                    ws, body.worker, role=role or "developer", host=body.host,
                    capabilities=body.capabilities, goal=body.goal,
                    owner=_principal_id(request) or "")
            except WorkerOwnershipError as e:
                raise HTTPException(status_code=403, detail=str(e))
            return w.to_dict()

        @router.post("/weave/workers/{worker_id}/heartbeat",
                     dependencies=[Depends(combined_auth)],
                     summary="Heartbeat; returns the worker's control-state (run/pause/stop)")
        async def worker_heartbeat(worker_id: str, body: HeartbeatRequest, request: Request):
            from weave.team.workers import WorkerOwnershipError
            try:
                return registry.heartbeat(
                    _ws(), worker_id, current_task=body.current_task,
                    step=body.step, owner=_principal_id(request))
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no worker '{worker_id}'")
            except WorkerOwnershipError as e:
                raise HTTPException(status_code=403, detail=str(e))

        @router.get("/weave/workers", dependencies=[Depends(combined_auth)],
                    summary="The live fleet (stale heartbeat → offline)")
        async def list_workers(include_offline: bool = True):
            ws = _ws()
            return {"workspace": ws,
                    "workers": registry.list(ws, include_offline=include_offline)}

        @router.post("/weave/workers/{worker_id}/control",
                     dependencies=[Depends(combined_auth)],
                     summary="Supervisor control: pause · resume · stop")
        async def control_worker(worker_id: str, body: ControlRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not steer the fleet (supervisors only)")
            # Through the supervisor, so the act carries **who** did it, not only
            # that someone with the right role did (A6, P5). `redirect` is new:
            # it changes what a worker is for without stopping it.
            from weave.team.supervisor import (
                NotAuthenticated, Supervisor, SupervisorError,
            )

            seat = Supervisor(registry, host_registry, coordinator)
            try:
                act = await seat.control_worker(
                    _ws(), worker_id, body.action,
                    by=_principal_id(request) or "", goal=getattr(body, "goal", "") or "",
                )
            except NotAuthenticated as e:
                raise HTTPException(status_code=401, detail=str(e))
            except SupervisorError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no worker '{worker_id}'")
            except ValueError as e:                       # terminal stop
                raise HTTPException(status_code=409, detail=str(e))
            return {**registry.get(_ws(), worker_id), "act": act.to_dict()}

    # ── the project (P8) — what this workspace's developers work on ────────
    if project_service is not None:

        @router.get("/weave/project", dependencies=[Depends(combined_auth)],
                    summary="The repository, base branch and test command for this workspace")
        async def get_project():
            ws = _ws()
            return {"workspace": ws, **project_service.get(ws).to_dict()}

        @router.put("/weave/project", dependencies=[Depends(combined_auth)],
                    summary="Define what the team works on (supervisors only)")
        async def set_project(body: ProjectRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not define the project (supervisors only)")
            p = project_service.set(
                _ws(), repo=body.repo, base_branch=body.base_branch, image=body.image,
                test_command=body.test_command, setup_command=body.setup_command,
                description=body.description, by=role or "")
            return p.to_dict()

    # ── dev hosts (P8) — machines that carry autonomous developers ─────────
    if host_registry is not None:

        @router.post("/weave/hosts/register", dependencies=[Depends(combined_auth)],
                     summary="Register this machine into the fleet of dev hosts")
        async def register_host(body: RegisterHostRequest, request: Request):
            from weave.devhost.registry import HostOwnershipError
            ws = _ws()
            role = _role(request)
            if rbac_service is not None:
                d = rbac_service.check(ws, role, "invoke", "RegisterDevHost")
                if not d.allowed:
                    raise HTTPException(status_code=403, detail=d.reason)
            try:
                h = await host_registry.register(
                    ws, body.host, machine=body.machine, capabilities=body.capabilities,
                    repo=body.repo, base_branch=body.base_branch, image=body.image,
                    version=body.version, seat=body.seat, seat_detail=body.seat_detail,
                    owner=_principal_id(request) or "")
            except HostOwnershipError as e:
                raise HTTPException(status_code=403, detail=str(e))
            return h.to_dict()

        @router.post("/weave/hosts/{host_id}/heartbeat",
                     dependencies=[Depends(combined_auth)],
                     summary="Heartbeat; returns control-state + the desired worker count")
        async def host_heartbeat(host_id: str, body: HostHeartbeatRequest, request: Request):
            from weave.devhost.registry import HostOwnershipError
            try:
                return host_registry.heartbeat(
                    _ws(), host_id, workers=body.workers, seat=body.seat,
                    seat_detail=body.seat_detail, owner=_principal_id(request))
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no dev host '{host_id}'")
            except HostOwnershipError as e:
                raise HTTPException(status_code=403, detail=str(e))

        @router.get("/weave/hosts", dependencies=[Depends(combined_auth)],
                    summary="The dev-host fleet (stale heartbeat → offline)")
        async def list_hosts(include_offline: bool = True):
            ws = _ws()
            return {"workspace": ws,
                    "hosts": host_registry.list(ws, include_offline=include_offline)}

        @router.post("/weave/hosts/{host_id}/control",
                     dependencies=[Depends(combined_auth)],
                     summary="Supervisor control: drain · pause · resume · stop")
        async def control_host(host_id: str, body: ControlRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not steer the fleet (supervisors only)")
            if body.action not in ("drain", "pause", "resume", "stop"):
                raise HTTPException(status_code=400, detail=f"unknown action '{body.action}'")
            try:
                return host_registry.set_control(_ws(), host_id, body.action).to_dict()
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no dev host '{host_id}'")
            except ValueError as e:                       # terminal stop
                raise HTTPException(status_code=409, detail=str(e))

        @router.post("/weave/hosts/{host_id}/scale",
                     dependencies=[Depends(combined_auth)],
                     summary="How many developers the team wants this machine to run")
        async def scale_host(host_id: str, body: ScaleHostRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not size the fleet (supervisors only)")
            from weave.team.supervisor import (
                NotAuthenticated, Supervisor, SupervisorError,
            )

            seat = Supervisor(registry, host_registry, coordinator)
            try:
                act = await seat.scale_host(
                    _ws(), host_id, body.desired_workers,
                    by=_principal_id(request) or "")
            except NotAuthenticated as e:
                raise HTTPException(status_code=401, detail=str(e))
            except SupervisorError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no dev host '{host_id}'")
            return {**host_registry.get(_ws(), host_id), "act": act.to_dict()}

        # ── the senior-developer seat (P5) ────────────────────────────────
        #
        # Dispatch is scaling plus ordering, and **neither half reaches out**
        # (A15). The machines are asked — by state they read back on their next
        # heartbeat — to run more developers; the queue is ordered so that when
        # those developers wake and claim, they claim the right thing. Nothing
        # is started here, and the response says so.

        @router.post("/weave/team/dispatch", dependencies=[Depends(combined_auth)],
                     summary="Put N developers to work across the fleet")
        async def dispatch(body: DispatchRequest, request: Request):
            role = _role(request)
            if role not in SUPERVISOR_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"role '{role}' may not dispatch the fleet (supervisors only)")

            from weave.team.supervisor import (
                NotAuthenticated, Supervisor, SupervisorError,
            )

            seat = Supervisor(registry, host_registry, coordinator)
            try:
                return await seat.dispatch(
                    _ws(), by=_principal_id(request) or "",
                    hosts=body.hosts, workers_per_host=body.workers_per_host)
            except NotAuthenticated as e:
                raise HTTPException(status_code=401, detail=str(e))
            except SupervisorError as e:
                raise HTTPException(status_code=400, detail=str(e))

        @router.get("/weave/team/fleet", dependencies=[Depends(combined_auth)],
                    summary="Hosts and workers, with desired vs running per host")
        async def fleet():
            from weave.team.supervisor import Supervisor

            return Supervisor(registry, host_registry, coordinator).fleet(_ws())

    logger.info("Weave API routes registered")
    return router
