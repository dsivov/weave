"""Project layouts — register repositories, and resolve a locator to a document.

Endpoints (all workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  POST   /projects           — register a repository in the caller's workspace
  GET    /projects           — the repositories registered in it
  GET    /projects/resolve   — resolve `repo`/`path`/`rev` to a URL and content

A thin adapter over :class:`weave.model.project_layout.ProjectLayoutRegistry`.
The registry holds the rules; this module maps them onto status codes (A9).

**The 404 is the interesting part.** A locator naming a repository that is not
registered in the caller's workspace returns a bare 404 — not the content, and
not a distinguishable error. "No such repository" and "registered, but to
someone else" must look identical from outside, or the error message becomes a
way to enumerate other tenants' repositories. `resolve()` returns file content,
so this is the boundary that makes membership mean anything (R22a, A14, D-028).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from weave.model.locator import Locator, LocatorError
from weave.model.project_layout import (
    NotRegistered,
    ProjectLayoutError,
    ProjectLayoutRegistry,
)
from weave.server.utils import get_combined_auth_dependency


class ProjectRegistration(BaseModel):
    name: str = Field(
        min_length=1, max_length=128,
        description="The repository name a locator's `repo` field holds.",
    )
    clone_url: str = Field("", description="Where a human clones or browses it.")
    local_path: str = Field(
        "", description="A server-side checkout, so an agent can read content."
    )
    default_rev: str = Field("main", description="Assumed when a locator omits `rev`.")
    description: str = ""


class ProjectResponse(BaseModel):
    name: str
    clone_url: str = ""
    default_rev: str = "main"
    description: str = ""
    has_local_checkout: bool = False


class ProjectListResponse(BaseModel):
    workspace: str
    projects: List[ProjectResponse] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    repo: str
    path: str
    rev: str
    url: str = ""
    exists: bool = False
    anchor: Optional[str] = None
    content: Optional[str] = None
    size: Optional[int] = None
    truncated: Optional[bool] = None
    reason: Optional[str] = None


def create_project_routes(
    registry: ProjectLayoutRegistry,
    *,
    api_key: Optional[str] = None,
    workspace_resolver=None,
):
    """Build the /projects router bound to a ProjectLayoutRegistry."""
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(prefix="/projects", tags=["projects"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.post("", response_model=ProjectResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Register a repository in this workspace")
    def register_project(body: ProjectRegistration):
        try:
            layout = registry.register(
                _ws(),
                body.name,
                clone_url=body.clone_url,
                local_path=body.local_path,
                default_rev=body.default_rev,
                description=body.description,
            )
        except ProjectLayoutError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return layout.public_dict()

    @router.get("", response_model=ProjectListResponse,
                dependencies=[Depends(combined_auth)],
                summary="Repositories registered in this workspace")
    def list_projects():
        workspace = _ws()
        return {
            "workspace": workspace,
            "projects": [p.public_dict() for p in registry.list(workspace)],
        }

    @router.get("/resolve", response_model=ResolveResponse,
                dependencies=[Depends(combined_auth)],
                summary="Resolve a locator within this workspace")
    def resolve(
        repo: str = Query(..., description="Repository name, as the locator holds it"),
        path: str = Query(..., description="Path within the repository"),
        rev: str = Query("", description="Revision; the layout's default if omitted"),
        anchor: str = Query("", description="Optional anchor within the file"),
        content: bool = Query(True, description="Include file content when readable"),
    ) -> Dict[str, Any]:
        workspace = _ws()
        try:
            layout = registry.require(workspace, repo)
        except NotRegistered:
            # Bare 404, no detail. See the module docstring: this response must
            # be identical whether the repository is unknown or another
            # workspace's, or it enumerates other tenants (R22a).
            raise HTTPException(status_code=404, detail="Not found.")

        try:
            locator = Locator(
                repo=repo, path=path, rev=rev or layout.default_rev,
                anchor=anchor or None,
            )
        except LocatorError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return registry.resolve(workspace, locator, want_content=content)

    return router
