"""Flow engine API — author flows, run them, inspect and replay runs (P2).

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET    /flows              — the latest version of every authored flow
  POST   /flows              — save a flow (validated + lint); assigns next version
  GET    /flows/{id}         — one flow (``?version=`` pins an old version)
  DELETE /flows/{id}         — remove a flow (all versions)
  POST   /flows/{id}/dry-run — start a run from a synthetic event/vars, no wait
  GET    /runs               — list runs (``?status=`` / ``?app_id=`` filters)
  GET    /runs/{run_id}      — one run (cursor, status, state, history)
  GET    /runs/{run_id}/replay — re-walk the recorded history; reproduce or diff

A flow starts automatically when an ingress event matches its ``on_event`` (see
:class:`~weave_core.flows.trigger.FlowTrigger`); ``dry-run`` is the manual
path for authoring/testing. Available only in Weave mode.
See docs/PLATFORM_WORK_PLAN.md (P2).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import authenticated_principal, get_combined_auth_dependency
from weave_core.utils import logger


class FlowRequest(BaseModel):
    flow: Dict[str, Any] = Field(
        description="The flow as JSON (id, on_event, nodes, edges).")


class DryRunRequest(BaseModel):
    event_type: Optional[str] = Field(
        default=None, description="Event type; defaults to the flow's on_event.")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Synthetic event payload → run vars.")
    vars: Dict[str, Any] = Field(
        default_factory=dict, description="Extra run vars (override payload).")


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Flow engine requires Weave mode. Set WEAVE_ENABLE_QUADRUPLE=true.",
        )


def create_flow_routes(rag, flow_store, executor, *, studio_engine=None,
                       api_key: Optional[str] = None,
                       workspace_resolver=None):
    """Build the /flows + /runs router bound to *rag*, a FlowStore, and a
    FlowExecutor."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    from weave_core.events.schema import Event
    from weave_core.flows.schema import FlowDefinition

    # ── flows are signed into the ledger (A8, W12) ──────────────────────────
    #
    # A flow is **executed**, not descriptive: a `task` step dispatches to
    # `ActionService.invoke` — the same RBAC → lifecycle → rules-gate chain any
    # action passes — and `FlowTrigger` runs flows automatically on events. So an
    # unsigned flow is an automation that invokes governed actions with nobody's
    # name on it, and without a human in the loop at the moment it fires.
    #
    # `flow` has been a `DIFF_KINDS` member since P3 and the engine already
    # persists it. This router was writing round the side of that.

    def _require_ledger():
        if studio_engine is None:
            raise HTTPException(
                status_code=503,
                detail=("A flow must be signed into the ledger, and the Studio "
                        "engine is unavailable (A8, W12)."))
        return studio_engine

    def _signer(request: Request):
        principal = authenticated_principal(request)
        approver = str(principal.get("sub") or principal.get("username") or "")
        if not approver:
            raise HTTPException(
                status_code=401,
                detail="Saving a flow requires an authenticated identity.")
        return approver, str(principal.get("role") or "")

    router = APIRouter(tags=["flows"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    # -- authoring ----------------------------------------------------------

    @router.get("/flows", dependencies=[Depends(combined_auth)],
                summary="List authored flows (latest version each)")
    async def list_flows():
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws,
                "flows": [f.to_dict() for f in flow_store.list(ws)]}

    @router.post("/flows", dependencies=[Depends(combined_auth)],
                 summary="Save a flow (validated); assigns the next version")
    async def save_flow(body: FlowRequest, http_request: Request):
        _require_cg(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        try:
            # Validated here so a malformed flow is a 400 rather than surfacing
            # from inside the engine as a sign-off failure.
            flow = FlowDefinition.from_dict(body.flow)
            await engine.sign(ws, "flow", flow.to_dict(), approver=approver,
                              reason=f"flow '{flow.id}' saved by {approver}",
                              role=role, artifact_id=flow.id)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        stored = flow_store.get(ws, flow.id)
        return {"workspace": ws, "saved": True, "flow": stored.to_dict()}

    @router.get("/flows/{flow_id}", dependencies=[Depends(combined_auth)],
                summary="Get one flow (optionally a pinned version)")
    async def get_flow(flow_id: str, version: Optional[int] = None):
        _require_cg(rag)
        ws = _ws()
        flow = flow_store.get(ws, flow_id, version)
        if flow is None:
            raise HTTPException(status_code=404, detail=f"no flow '{flow_id}'")
        return flow.to_dict()

    @router.delete("/flows/{flow_id}", dependencies=[Depends(combined_auth)],
                   summary="Delete a flow (all versions)")
    async def delete_flow(flow_id: str, http_request: Request):
        _require_cg(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        # Removing an automation is a governance change, and it carries the same
        # ambiguity every other removal did: an empty snapshot alone cannot say
        # whether the flow was deleted or authored empty. `origin='removal'`
        # carries it.
        result = await engine.sign_removal(
            ws, "flow", approver=approver,
            reason=f"flow '{flow_id}' removed by {approver}", role=role,
            artifact_id=flow_id)
        return {"deleted": result is not None, "recorded": result}

    @router.post("/flows/{flow_id}/dry-run", dependencies=[Depends(combined_auth)],
                 summary="Start a run from a synthetic event")
    async def dry_run(flow_id: str, body: DryRunRequest):
        _require_cg(rag)
        ws = _ws()
        flow = flow_store.get(ws, flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail=f"no flow '{flow_id}'")
        event = Event(
            type=body.event_type or flow.on_event,
            payload=dict(body.payload),
            source="dry-run",
            workspace=ws,
        )
        try:
            run = await executor.start(ws, flow, event=event, vars=body.vars)
        except Exception as e:
            logger.error(f"flow dry-run '{flow_id}' failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
        return run.to_dict()

    # -- runs ---------------------------------------------------------------

    @router.get("/runs", dependencies=[Depends(combined_auth)],
                summary="List runs (filter by status / app_id)")
    async def list_runs(status: Optional[str] = None, app_id: Optional[str] = None):
        _require_cg(rag)
        ws = _ws()
        runs = await executor.run_store.list(ws, app_id=app_id, status=status)
        return {"workspace": ws, "count": len(runs),
                "runs": [r.to_dict() for r in runs]}

    @router.get("/runs/{run_id}", dependencies=[Depends(combined_auth)],
                summary="Get one run")
    async def get_run(run_id: str):
        _require_cg(rag)
        run = await executor.run_store.get(_ws(), run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        return run.to_dict()

    @router.get("/runs/{run_id}/replay", dependencies=[Depends(combined_auth)],
                summary="Replay a run's history against its pinned flow")
    async def replay_run(run_id: str):
        _require_cg(rag)
        ws = _ws()
        run = await executor.run_store.get(ws, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        result = await executor.replay(ws, run)
        return {
            "run_id": run_id,
            "ok": result.ok,
            "status": result.status,
            "state": result.state,
            "path": result.path,
            "mismatches": result.mismatches,
        }

    logger.info("Flow engine API routes registered")
    return router
