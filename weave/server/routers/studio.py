"""Studio API — diff-and-approve authoring of governed artifacts (P3).

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  POST /studio/propose            — draft the next version of an artifact → a diff
  POST /studio/assess             — (re)compute behaviour_changed for a diff
  POST /studio/apply              — persist + sign off a diff → a new version
  POST /studio/revert             — re-apply a prior version's snapshot (signed)
  GET  /studio/artifacts          — every artifact the Studio tracks here
  GET  /studio/history/{kind}/{id}— the signed version ledger for one artifact

``propose`` returns an already-assessed diff (behaviour_changed set) for the UI;
``apply`` re-assesses server-side before enforcing sign-off, so the flag can't be
tampered with in transit. Available only in Weave mode.
See docs/PLATFORM_WORK_PLAN.md (P3).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import authenticated_principal, get_combined_auth_dependency
from weave_core.utils import logger


class ProposeRequest(BaseModel):
    kind: str = Field(description="ontology | rule | flow | action")
    artifact_id: str = Field(description="Artifact key (flow id, or a label).")
    draft: Optional[Dict[str, Any]] = Field(
        default=None, description="A ready artifact dict (deterministic authoring).")
    spec: Optional[str] = Field(
        default=None, description="Natural-language spec (ontology/rule; needs an LLM).")
    concepts: Optional[Dict[str, List[str]]] = Field(default=None)
    origin: str = Field(default="authoring", description="authoring | migration | reapproval")


class DiffBody(BaseModel):
    diff: Dict[str, Any] = Field(description="An ArtifactDiff as returned by /studio/propose.")


class ApplyRequest(BaseModel):
    diff: Dict[str, Any]
    reason: Optional[str] = None
    # `approver` and `role` are DELETED, not ignored (D-038). They used to be
    # accepted here and written straight into the sign-off, so a caller could
    # sign a governance change as anybody — `Studio.tsx` even rendered a text box
    # for it, and validated only that the name was non-empty, never that it was
    # yours. A6 says the principal is derived from the authenticated identity and
    # never from a client-supplied field; this is that, enforced by the field not
    # existing. Silently ignoring them would leave the next reader thinking they
    # worked.


class RevertRequest(BaseModel):
    kind: str
    artifact_id: str
    to_version: int
    reason: str
    # Same as ApplyRequest: the identity is not the caller's to state. Reverting
    # is a governance change like any other — it re-applies an old snapshot as a
    # NEW signed version, so it needs a real signer just as much as the edit that
    # is being undone did.


class DraftRequest(BaseModel):
    kind: str = Field(description="ontology | rule (kinds with an NL author)")
    artifact_id: str
    instruction: str = Field(description="The latest chat message.")
    history: List[Dict[str, str]] = Field(
        default_factory=list, description="Prior turns [{role, content}] — re-sent each turn.")


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Studio requires Weave mode. Set WEAVE_ENABLE_QUADRUPLE=true.",
        )


def create_studio_routes(rag, engine, *, api_key: Optional[str] = None,
                         workspace_resolver=None):
    """Build the /studio router bound to *rag* and a DiffEngine."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    from weave_core.studio.schema import ArtifactDiff

    def _signer(request: Request):
        """Who is signing — from the token, never from the body (A6, D-038).

        Copied deliberately from `routers/wizard.py`, which has always had this
        right, rather than invented here: A8's signature has to name somebody,
        and an unattributed governance change makes *"who took away my access"*
        unanswerable. 401 rather than a blank signer, because a ledger entry
        signed by nobody is the same failure as one signed by anybody.
        """
        principal = authenticated_principal(request)
        approver = str(principal.get("sub") or principal.get("username") or "")
        if not approver:
            raise HTTPException(
                status_code=401,
                detail="Signing a governance change requires an authenticated identity.")
        return approver, str(principal.get("role") or "")

    router = APIRouter(tags=["studio"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.post("/studio/propose", dependencies=[Depends(combined_auth)],
                 summary="Draft the next version of an artifact as a diff")
    async def propose(body: ProposeRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            diff = await engine.propose(
                ws, body.kind, body.artifact_id,
                draft=body.draft, spec=body.spec, concepts=body.concepts,
                origin=body.origin)
            engine.assess(ws, diff)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"diff": diff.to_dict()}

    @router.post("/studio/assess", dependencies=[Depends(combined_auth)],
                 summary="Recompute behaviour_changed for a diff")
    async def assess(body: DiffBody):
        _require_cg(rag)
        diff = ArtifactDiff.from_dict(body.diff)
        engine.assess(_ws(), diff)
        return {"diff": diff.to_dict()}

    @router.post("/studio/apply", dependencies=[Depends(combined_auth)],
                 summary="Persist + sign off a diff → a new version")
    async def apply(body: ApplyRequest, http_request: Request):
        _require_cg(rag)
        ws = _ws()
        approver, role = _signer(http_request)
        from weave_core.governance.rules.gate import RuleViolation

        from weave_core.studio.service import StaleWrite

        diff = ArtifactDiff.from_dict(body.diff)
        engine.assess(ws, diff)                     # re-assess server-side (anti-tamper)
        try:
            result = await engine.apply(
                ws, diff, approver=approver, reason=body.reason, role=role)
        except StaleWrite as e:
            # 409 with the merge view, never a silent overwrite (R31). The body
            # carries base/theirs/mine so the client can reconcile — a bare 409
            # leaves someone holding an edit they cannot land.
            raise HTTPException(status_code=409, detail=e.to_dict())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except RuleViolation as e:
            raise HTTPException(status_code=422, detail={
                "status": "rejected", "outcome": "REJECT", "audit": e.decision.audit})
        return result

    @router.post("/studio/draft", dependencies=[Depends(combined_auth)],
                 summary="Conversationally author a diff from a chat (AI)")
    async def draft(body: DraftRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            return await engine.draft(
                ws, body.kind, body.artifact_id, body.instruction, history=body.history)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/studio/revert", dependencies=[Depends(combined_auth)],
                 summary="Re-apply a prior version's snapshot as a new signed version")
    async def revert(body: RevertRequest, http_request: Request):
        _require_cg(rag)
        approver, role = _signer(http_request)
        try:
            return await engine.revert(
                _ws(), body.kind, body.artifact_id, body.to_version,
                approver=approver, reason=body.reason, role=role)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/studio/artifacts", dependencies=[Depends(combined_auth)],
                summary="Every artifact the Studio tracks in this workspace")
    async def artifacts():
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, "artifacts": engine.artifacts(ws)}

    @router.get("/studio/graph", dependencies=[Depends(combined_auth)],
                summary="Cross-artifact relationship map (flows↔actions↔rules↔ontology)")
    async def graph():
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, **engine.component_graph(ws)}

    @router.get("/studio/history/{kind}/{artifact_id}",
                dependencies=[Depends(combined_auth)],
                summary="The signed version ledger for one artifact")
    async def history(kind: str, artifact_id: str):
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, "kind": kind, "artifact_id": artifact_id,
                "history": engine.history(ws, kind, artifact_id)}

    logger.info("Studio API routes registered")
    return router
