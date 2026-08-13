"""RBAC API — manage a workspace's role-based access policy (P3, Gap 1).

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET    /rbac          — summary: roles → grants, version
  POST   /rbac          — set/replace the workspace policy (validated)
  DELETE /rbac          — remove the policy (workspace reverts to permissive)
  POST   /rbac/check    — dry-run an access decision (role, verb, target)

Opt-in and deny-by-default *within* a policy; a workspace with no policy is
permissive. Enforcement of the policy happens as a pre-check on
``/actions/invoke``. Available only in Weave mode. See docs/RBAC_SPEC.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import authenticated_principal, get_combined_auth_dependency
from weave_core.utils import logger


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class RbacPolicyRequest(BaseModel):
    policy: Dict[str, Any] = Field(
        description="The RBAC policy as JSON (name, roles: {role: [grants]}).")


class RbacSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    name: Optional[str] = None
    version: Optional[int] = None
    updated_at: Optional[float] = None
    roles: Dict[str, List[str]] = Field(default_factory=dict)


class CheckRequest(BaseModel):
    role: Optional[str] = Field(default=None, description="The principal's role.")
    verb: str = Field(default="invoke", description="invoke | create | update | delete | read")
    target: str = Field(description="Action name or object type.")
    object_ref: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="RBAC requires the governance engine. Set WEAVE_ENABLE_QUADRUPLE=true (separate from WEAVE_ENABLE_TEAM).",
        )


def create_rbac_routes(rag, service, *, studio_engine=None,
                       api_key: Optional[str] = None,
                       workspace_resolver=None):
    """Build the /rbac router bound to *rag* and an RbacService."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    # ── governance writes go through the signed ledger (A8, D-033) ──────────
    #
    # These endpoints change what the runtime enforces. Writing straight through
    # `service.save()` left no signature and no version — false by A8's first
    # sentence, and the guard that was supposed to catch it *excluded this file*
    # on the reasoning that it "is the direct surface". A8 does not care which
    # surface is meant to be direct.
    #
    # `studio_engine` is required rather than optional: falling back to the
    # direct write is how a removed second path comes back.

    def _require_ledger():
        if studio_engine is None:
            raise HTTPException(
                status_code=503,
                detail=("This change must be signed into the ledger, and the "
                        "Studio engine is unavailable (A8, D-033)."))
        return studio_engine

    def _signer(request: Request):
        principal = authenticated_principal(request)
        approver = str(principal.get("sub") or principal.get("username") or "")
        if not approver:
            raise HTTPException(
                status_code=401,
                detail="Changing governance requires an authenticated identity.")
        return approver, str(principal.get("role") or "")

    router = APIRouter(tags=["rbac"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/rbac", response_model=RbacSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's RBAC policy")
    async def get_rbac():
        _require_cg(rag)
        return RbacSummaryResponse(**service.get_summary(_ws()))

    @router.post("/rbac", response_model=RbacSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's RBAC policy (validated)")
    async def set_rbac(request: RbacPolicyRequest, http_request: Request):
        _require_cg(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        try:
            await engine.sign(ws, "rbac", request.policy, approver=approver,
                              reason=f"RBAC policy set by {approver}", role=role)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return RbacSummaryResponse(**service.get_summary(ws))

    @router.delete("/rbac", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's RBAC policy (→ permissive)")
    async def delete_rbac(http_request: Request):
        _require_cg(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        # A removal is a governance change and gets a version recording it, not
        # an absence — this endpoint returns the workspace to **permissive**,
        # which is the most consequential thing anyone can do here.
        result = await engine.sign_removal(
            ws, "rbac", approver=approver,
            reason=f"RBAC policy removed by {approver} (workspace → permissive)",
            role=role)
        return {"deleted": result is not None, "workspace": ws, "recorded": result}

    @router.post("/rbac/check", dependencies=[Depends(combined_auth)],
                 summary="Dry-run an access decision")
    async def check_rbac(request: CheckRequest):
        _require_cg(rag)
        d = service.check(_ws(), request.role, request.verb, request.target,
                          object_ref=request.object_ref, rag=rag)
        return {"allowed": d.allowed, "reason": d.reason}

    logger.info("RBAC API routes registered")
    return router
