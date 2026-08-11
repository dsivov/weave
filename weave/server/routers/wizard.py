"""The team-vocabulary wizard, over HTTP.

Endpoints (workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET  /wizard/templates  — the team shapes on offer
  POST /wizard/session    — the interview plan for one template
  POST /wizard/propose    — answers → signed-ledger diffs, nothing written
  POST /wizard/apply      — sign the diffs → new ledger versions

**Apply writes through the ledger, never to a file** (A8, R39). Each diff goes
through the same `DiffEngine.apply` the Studio uses, so a governance change gets
a signature, a version, a diff and history — and the runtime enforces it on the
**next request**, with no restart, because what the runtime reads *is* the thing
that was written.

**There is no wizard-only code path.** Sign-off, the workspace rule gate, and the
stale-write check all live in `DiffEngine.apply`; this router chooses artifacts
and passes them on. That is A9, and it is also why the P3.3 decision to put the
version check in the engine rather than the router matters here: a wizard run
gets it for free.

`/wizard/session` holds nothing server-side — see `weave/wizards/session.py` for
why a session dict would break the moment a second worker existed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import authenticated_principal, get_combined_auth_dependency
from weave.wizards import TEMPLATES, WizardError, plan_for, propose_diffs
from weave.wizards.session import WIZARD_KINDS, catalogue


class SessionRequest(BaseModel):
    template: str = Field(description=f"One of: {', '.join(TEMPLATES)}")


class ProposeRequest(BaseModel):
    template: str
    answers: Dict[str, Any] = Field(default_factory=dict)


class ApplyRequest(BaseModel):
    diffs: List[Dict[str, Any]] = Field(
        description="The diffs from /wizard/propose, reviewed and unchanged."
    )
    reason: str = Field("", description="Why this governance is being installed.")


def create_wizard_routes(
    rag,
    engine,
    *,
    api_key: Optional[str] = None,
    workspace_resolver=None,
):
    """Build the /wizard router over an existing Studio `DiffEngine`."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(prefix="/wizard", tags=["wizard"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _require_engine():
        if engine is None:
            raise HTTPException(
                status_code=503,
                detail="The wizard needs the Studio engine (Weave mode).",
            )
        return engine

    @router.get("/templates", dependencies=[Depends(combined_auth)],
                summary="The team shapes on offer")
    async def templates():
        return {"templates": catalogue()}

    @router.post("/session", dependencies=[Depends(combined_auth)],
                 summary="The interview plan for one template")
    async def session(body: SessionRequest):
        try:
            return plan_for(body.template)
        except WizardError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/propose", dependencies=[Depends(combined_auth)],
                 summary="Answers → diffs. Writes nothing.")
    async def propose(body: ProposeRequest):
        studio = _require_engine()
        workspace = _ws()

        # Read what is there now, so each diff records the version it was drafted
        # against and the stale-write check has something to compare (P3.3).
        current = {
            kind: studio._load_current(workspace, kind, kind)
            for kind in WIZARD_KINDS
        }
        try:
            diffs = propose_diffs(body.template, body.answers, current=current)
        except WizardError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Assessed exactly as a Studio diff is, so the wizard cannot smuggle a
        # change past the checks an ordinary edit faces.
        for diff in diffs:
            studio.assess(workspace, diff)

        return {
            "workspace": workspace,
            "template": body.template,
            "diffs": [d.to_dict() for d in diffs],
            "count": len(diffs),
        }

    @router.post("/apply", dependencies=[Depends(combined_auth)],
                 summary="Sign the diffs into new ledger versions")
    async def apply(body: ApplyRequest, request: Request):
        from weave_core.governance.rules.gate import RuleViolation
        from weave_core.studio.schema import ArtifactDiff
        from weave_core.studio.service import StaleWrite

        studio = _require_engine()
        workspace = _ws()

        principal = authenticated_principal(request)
        approver = str(principal.get("sub") or principal.get("username") or "")
        role = str(principal.get("role") or "")
        if not approver:
            # A8's signature has to name somebody. An unattributed governance
            # change makes "who took away my access" unanswerable.
            raise HTTPException(
                status_code=401,
                detail="Installing governance requires an authenticated identity.",
            )

        reason = body.reason.strip() or f"team-vocabulary wizard, applied by {approver}"
        applied: List[Dict[str, Any]] = []
        for raw in body.diffs:
            diff = ArtifactDiff.from_dict(raw)
            if diff.kind not in WIZARD_KINDS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"the wizard installs {list(WIZARD_KINDS)}, not '{diff.kind}' "
                        "— use /studio/apply for other artifact kinds"
                    ),
                )
            # Re-assessed server-side: the client round-tripped these diffs, so
            # what comes back is not trusted (the same anti-tamper reasoning as
            # /studio/apply).
            studio.assess(workspace, diff)
            try:
                applied.append(
                    await studio.apply(
                        workspace, diff, approver=approver, reason=reason, role=role
                    )
                )
            except StaleWrite as e:
                raise HTTPException(status_code=409, detail=e.to_dict())
            except RuleViolation as e:
                raise HTTPException(status_code=422, detail={
                    "status": "rejected", "outcome": "REJECT",
                    "audit": e.decision.audit})
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        return {
            "workspace": workspace,
            "applied": applied,
            "count": len(applied),
            # Said explicitly because it is the gate criterion: nothing was
            # written to a file and nothing needs restarting.
            "restart_required": False,
        }

    return router
