"""Business Rules API — manage and dry-run a workspace's rules gate (step 7).

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET    /rules            — summary: enabled, version, concepts, parsed rules
  POST   /rules            — set/replace the workspace policy (dsl + concepts); validates
  POST   /rules/evaluate   — dry-run the saved policy against a sample decision
  POST   /rules/toggle     — enable/disable the gate for the workspace
  DELETE /rules            — remove the workspace policy
  POST   /rules/generate   — NL → DSL (step 6; returns 501 until built)

Mutations attach the rebuilt gate to the live workspace instance, so the next
``/graph/decision/emit`` enforces the new policy immediately.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.utils import authenticated_principal, get_combined_auth_dependency
from weave.server.routers.reasoning import (
    RelationContextData,
    _pydantic_to_rc,
)
from weave_core.utils import logger


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class RulePolicyRequest(BaseModel):
    dsl: str = Field(description="The business-rule DSL (when/then rules).")
    concepts: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Concept catalog: name → example phrases for sim() matching.",
    )
    enabled: bool = Field(default=True, description="Whether the gate is active.")
    model_id: Optional[str] = Field(
        default=None, description="Override the pinned similarity model id."
    )


class RuleInfo(BaseModel):
    name: str
    priority: int = 0


class RuleSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    enabled: bool
    version: Optional[int] = None
    model_id: Optional[str] = None
    updated_at: Optional[float] = None
    concepts: List[str] = Field(default_factory=list)
    rules: List[RuleInfo] = Field(default_factory=list)
    dsl: str = ""
    concepts_map: Dict[str, List[str]] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    src: str
    tgt: str
    relation_type: str
    relation_context: RelationContextData
    as_of: Optional[str] = Field(
        default=None, description="ISO date for is_active evaluation (default today)."
    )


class EvaluateResponse(BaseModel):
    active: bool = Field(description="False if no enabled gate exists for the workspace.")
    outcome: Optional[str] = Field(default=None, description="PASS / FLAG / REJECT.")
    audit: Optional[Dict[str, Any]] = None
    triggered: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ToggleRequest(BaseModel):
    enabled: bool


class GenerateRequest(BaseModel):
    policy: str = Field(description="The policy to encode, in plain English.")
    concepts: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Seed concept catalog; merged with the workspace's existing concepts.",
    )
    use_stored_concepts: bool = Field(
        default=True, description="Seed generation with the workspace's saved concepts."
    )
    max_repairs: int = Field(default=1, ge=0, le=3,
                             description="Auto-repair rounds if a draft fails validation.")
    save: bool = Field(
        default=False,
        description="If true and the draft is valid, persist + enable it (default: review-only).",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_rules_capable(rag) -> None:
    # A WeaveGraph instance carries a ``rules_gate`` slot; plain WeaveEngine doesn't.
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Rules require Weave mode. Set WEAVE_ENABLE_QUADRUPLE=true.",
        )


def create_rules_routes(rag, service, *, studio_engine=None, api_key: Optional[str] = None,
                        workspace_resolver=None):
    """Build the /rules router bound to *rag* and a RulesService.

    workspace_resolver: callable returning the current workspace name; defaults
    to the request-scoped ``WEAVE-WORKSPACE`` contextvar.
    """
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    # ── governance writes go through the signed ledger (A8, D-033) ──────────
    #
    # These endpoints change what the runtime enforces. Writing straight through
    # `service.save()` left no signature and no version — false by A8's first
    # sentence, and the guard meant to catch it *excluded this file* on the
    # reasoning that it "is the direct surface". A8 does not care which surface
    # is meant to be direct.
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

    router = APIRouter(tags=["rules"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/rules", response_model=RuleSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's rules policy")
    async def get_rules():
        _require_rules_capable(rag)
        return RuleSummaryResponse(**service.get_summary(_ws()))

    @router.post("/rules", response_model=RuleSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's rules policy (validated)")
    async def set_rules(request: RulePolicyRequest, http_request: Request):
        _require_rules_capable(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        try:
            await engine.sign(
                ws, "rule",
                {"dsl": request.dsl,
                 "concepts": {k: list(v) for k, v in (request.concepts or {}).items()},
                 "enabled": request.enabled},
                approver=approver, reason=f"rules set by {approver}", role=role)
        except ValueError as e:
            # Invalid DSL / undefined concept → 400 (author error)
            raise HTTPException(status_code=400, detail=str(e))
        service.attach(rag, ws)  # live enforcement on the next emit
        return RuleSummaryResponse(**service.get_summary(ws))

    @router.post("/rules/evaluate", response_model=EvaluateResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Dry-run the saved policy against a sample decision")
    async def evaluate_rules(request: EvaluateRequest):
        _require_rules_capable(rag)
        rc = _pydantic_to_rc(request.relation_context)
        decision = service.evaluate(_ws(), request.src, request.tgt,
                                    request.relation_type, rc, as_of=request.as_of)
        if decision is None:
            return EvaluateResponse(active=False)
        result = decision.result
        triggered = [
            {"rule": m.rule, "severity": m.severity, "reason": m.reason,
             "matches": m.matches}
            for m in result.triggered
        ]
        return EvaluateResponse(
            active=True, outcome=decision.outcome, audit=decision.audit,
            triggered=triggered, warnings=result.warnings, notes=result.notes,
        )

    @router.post("/rules/toggle", response_model=RuleSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Enable/disable the workspace gate")
    async def toggle_rules(request: ToggleRequest):
        _require_rules_capable(rag)
        ws = _ws()
        try:
            service.set_enabled(ws, request.enabled)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No policy for workspace '{ws}'.")
        service.attach(rag, ws)
        return RuleSummaryResponse(**service.get_summary(ws))

    @router.delete("/rules", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's rules policy")
    async def delete_rules(http_request: Request):
        _require_rules_capable(rag)
        ws = _ws()
        engine = _require_ledger()
        approver, role = _signer(http_request)
        result = await engine.sign_removal(
            ws, "rule", approver=approver,
            reason=f"rules policy removed by {approver}", role=role)
        service.attach(rag, ws)  # detaches (gate becomes None)
        return {"deleted": result is not None, "workspace": ws, "recorded": result}

    @router.post("/rules/generate", dependencies=[Depends(combined_auth)],
                 summary="Generate (and optionally apply) DSL from a natural-language policy")
    async def generate_rules(request: GenerateRequest, http_request: Request):
        _require_rules_capable(rag)
        from weave_core.governance.rules.agent import RuleAuthor

        llm = getattr(rag, "llm_model_func", None)
        if llm is None:
            raise HTTPException(status_code=503, detail="No LLM is configured for this workspace.")

        ws = _ws()
        seed = dict(request.concepts)
        if request.use_stored_concepts:
            existing = service.store.load(ws)
            if existing is not None:
                seed = {**existing.concepts, **seed}

        author = RuleAuthor(llm, gate_backend=service.gate_backend)
        result = await author.generate(request.policy, concepts=seed,
                                       max_repairs=request.max_repairs)

        saved = False
        if request.save and result.valid:
            engine = _require_ledger()
            approver, role = _signer(http_request)
            await engine.sign(
                ws, "rule",
                {"dsl": result.dsl,
                 "concepts": {k: list(v) for k, v in (result.concepts or {}).items()},
                 "enabled": True},
                approver=approver,
                reason=f"rules authored from a policy description by {approver}",
                role=role)
            service.attach(rag, ws)
            saved = True

        return {**result.to_dict(), "saved": saved}

    logger.info("Rules API routes registered")
    return router
