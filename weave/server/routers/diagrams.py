"""Diagram API — shared, versioned project diagrams (P6).

Diagrams live on the **server**, per workspace, so a team shares one set: the
Architect draws it, the Manager reads it, a developer session fetches it by id.
Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET    /diagrams                  — every diagram here (metadata, no source)
  GET    /diagrams/{id}             — one diagram, latest or ``?version=``
  GET    /diagrams/{id}/versions    — its signed revision history
  GET    /diagrams/{id}/export      — the raw mermaid (``.mmd``) or a markdown block
  POST   /diagrams                  — save (governed: propose → assess → sign → apply)
  DELETE /diagrams/{id}             — remove it from the workspace

Saving deliberately runs the Studio gesture rather than writing a file directly:
a diagram is a signed artifact, so every save is assessed (structural change vs.
restyle), recorded as a decision, and appended to the ledger for history/revert.
Available only in Weave mode. See docs/WEAVE_RFC.html (P6).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from weave.server.utils import get_combined_auth_dependency
from weave_core.utils import logger


class SaveDiagramRequest(BaseModel):
    id: str = Field(description="Stable diagram id — the key teammates fetch it by.")
    source: str = Field(default="", description="Mermaid source.")
    title: str = Field(default="")
    description: str = Field(default="")
    depicts: List[str] = Field(
        default_factory=list,
        description="Change requests / modules / task ids this diagram is about.")
    tags: List[str] = Field(default_factory=list)
    spec: Optional[str] = Field(
        default=None,
        description="Natural-language description — drafts the mermaid with the AI "
                    "author instead of supplying `source` (needs an LLM).")
    approver: Optional[str] = Field(
        default=None, description="Required when the change alters the diagram's structure.")
    reason: Optional[str] = Field(default=None, description="Why — recorded as the sign-off.")
    role: Optional[str] = None


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Diagrams require Weave mode. Set WEAVE_ENABLE_QUADRUPLE=true.",
        )


def _summary(d) -> Dict[str, Any]:
    return {
        "id": d.id,
        "title": d.title,
        "description": d.description,
        "type": d.diagram_type(),
        "version": d.version,
        "depicts": list(d.depicts),
        "tags": list(d.tags),
    }


def create_diagram_routes(rag, engine, diagram_store, *, api_key: Optional[str] = None,
                          workspace_resolver=None):
    """Build the /diagrams router bound to *rag*, a DiffEngine, and a DiagramStore."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["diagrams"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _load(ws: str, diagram_id: str, version: Optional[int] = None):
        d = diagram_store.get(ws, diagram_id, version)
        if d is None:
            raise HTTPException(
                status_code=404,
                detail=f"no diagram '{diagram_id}'"
                       + (f" at version {version}" if version else "") + f" in workspace '{ws}'")
        return d

    @router.get("/diagrams", dependencies=[Depends(combined_auth)],
                summary="Every diagram shared in this workspace")
    async def list_diagrams(
        depicts: Optional[str] = Query(
            default=None, description="Only diagrams depicting this change request / module / task."),
    ):
        _require_cg(rag)
        ws = _ws()
        items = (diagram_store.depicting(ws, depicts) if depicts
                 else diagram_store.list(ws))
        return {"workspace": ws, "diagrams": [_summary(d) for d in items]}

    @router.get("/diagrams/{diagram_id}", dependencies=[Depends(combined_auth)],
                summary="One diagram with its mermaid source")
    async def get_diagram(diagram_id: str, version: Optional[int] = Query(default=None)):
        _require_cg(rag)
        ws = _ws()
        d = _load(ws, diagram_id, version)
        return {"workspace": ws, **d.to_dict(), "type": d.diagram_type()}

    @router.get("/diagrams/{diagram_id}/versions", dependencies=[Depends(combined_auth)],
                summary="The signed revision history for one diagram")
    async def diagram_versions(diagram_id: str):
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, "id": diagram_id,
                "history": engine.history(ws, "diagram", diagram_id)}

    @router.get("/diagrams/{diagram_id}/export", dependencies=[Depends(combined_auth)],
                response_class=PlainTextResponse,
                summary="The raw mermaid source, for a session or a doc")
    async def export_diagram(
        diagram_id: str,
        version: Optional[int] = Query(default=None),
        format: str = Query(default="mmd", description="mmd | md"),
    ):
        _require_cg(rag)
        d = _load(_ws(), diagram_id, version)
        if format == "md":
            heading = f"# {d.title or d.id}\n\n" + (f"{d.description}\n\n" if d.description else "")
            return PlainTextResponse(f"{heading}```mermaid\n{d.source.strip()}\n```\n")
        return PlainTextResponse(d.source)

    @router.post("/diagrams", dependencies=[Depends(combined_auth)],
                 summary="Save a diagram to the shared workspace (signed)")
    async def save_diagram(body: SaveDiagramRequest):
        _require_cg(rag)
        ws = _ws()
        from weave_core.governance.rules.gate import RuleViolation

        if not body.spec and not (body.source or "").strip():
            raise HTTPException(status_code=400, detail="supply either `source` or `spec`")

        draft = None
        if not body.spec:
            draft = {"id": body.id, "source": body.source, "title": body.title,
                     "description": body.description, "depicts": body.depicts,
                     "tags": body.tags}
        try:
            diff = await engine.propose(ws, "diagram", body.id, draft=draft, spec=body.spec)
            engine.assess(ws, diff)
            result = await engine.apply(ws, diff, approver=body.approver,
                                        reason=body.reason, role=body.role)
        except ValueError as e:
            # A missing sign-off on a structural change is a 422; bad mermaid is a 400.
            status = 422 if "sign-off" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))
        except RuleViolation as e:
            raise HTTPException(status_code=422, detail={
                "status": "rejected", "outcome": "REJECT", "audit": e.decision.audit})
        saved = diagram_store.get(ws, body.id)
        return {"workspace": ws, **result,
                "diagram": _summary(saved) if saved is not None else None}

    @router.delete("/diagrams/{diagram_id}", dependencies=[Depends(combined_auth)],
                   summary="Remove a diagram from the workspace")
    async def delete_diagram(diagram_id: str):
        _require_cg(rag)
        ws = _ws()
        if not diagram_store.delete(ws, diagram_id):
            raise HTTPException(status_code=404, detail=f"no diagram '{diagram_id}'")
        return {"status": "deleted", "workspace": ws, "id": diagram_id}

    logger.info("Diagram API routes registered")
    return router
