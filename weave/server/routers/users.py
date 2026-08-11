"""User administration — the screens the source never had (R13, D-009).

Thin adapters over :class:`weave.server.users.UserService`. The router decides
nothing: it authenticates, validates, calls a service function, and turns what
comes back into a status code. Anything it decided for itself would be a second
source of truth (A9).

**No response on this router can contain a password hash.** Every path returns
``User.public_dict()``, which has no field for one — the guarantee is in the
shape rather than in remembering to strip it (R17).

Administration is restricted to the ``admin`` role, checked against the
*authenticated* identity in the token, never a field in the request body (A6).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weave.server.users import (
    ACTIVE,
    ADMIN_ROLES,
    DISABLED,
    UserConflict,
    UserError,
    UserNotFound,
    UserService,
)
from weave.server.utils import authenticated_principal, get_combined_auth_dependency

__all__ = ["ADMIN_ROLES", "create_user_routes"]


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8)
    role: str = "user"
    display_name: str = ""
    email: str = ""
    workspaces: List[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class PasswordUpdate(BaseModel):
    password: str = Field(min_length=8)


class WorkspaceGrants(BaseModel):
    workspaces: List[str] = Field(default_factory=list)


def create_user_routes(users: UserService, api_key: Optional[str] = None) -> APIRouter:
    """Build the router.

    The handlers below are deliberately **synchronous**. The store is a
    ``RecordStore``, and on the PostgreSQL path a call blocks for the length of a
    round trip; FastAPI runs `def` endpoints in a threadpool, so that blocking
    costs one worker thread instead of the whole event loop. Writing them
    `async def` and calling a blocking store would stall every other request on
    the process for the duration.
    """
    router = APIRouter(prefix="/users", tags=["users"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _require_admin(request: Request) -> str:
        """403 unless the authenticated identity holds an administering role.

        **A dependency, not a call inside the handler**, and that ordering is
        the point: FastAPI resolves dependencies before it validates a request
        body, so an unauthorised caller gets 403 rather than a 422 explaining
        what the body should have looked like. Checking inside the handler
        hands the schema to exactly the people who were refused.

        Bootstrapping is the one exception and it is deliberate: while no user
        exists, the server is already handing out guest tokens to everybody, so
        refusing to let anyone create the first account would leave a fresh
        install with no way in that is not editing a file — the exact thing
        this milestone removes. The window closes the instant the first user is
        created, and it cannot be reopened by deleting them (see below).
        """
        if not users.any_user_exists:
            return "bootstrap"
        info = authenticated_principal(request)
        role = info.get("role", "")
        if role not in ADMIN_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Administering users requires an admin role.",
            )
        return info.get("username", "")

    @router.get("", dependencies=[Depends(combined_auth)])
    def list_users(actor: str = Depends(_require_admin)):
        return [u.public_dict() for u in users.list_users()]

    @router.post("", status_code=201, dependencies=[Depends(combined_auth)])
    def create_user(body: UserCreate, actor: str = Depends(_require_admin)):
        try:
            user = users.create(
                username=body.username,
                password=body.password,
                role=body.role,
                display_name=body.display_name,
                email=body.email,
                workspaces=body.workspaces,
                granted_by=actor,
            )
        except UserConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except UserError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return user.public_dict()

    @router.get("/{user_id}", dependencies=[Depends(combined_auth)])
    def get_user(user_id: str, actor: str = Depends(_require_admin)):
        try:
            return users.require(user_id).public_dict()
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.patch("/{user_id}", dependencies=[Depends(combined_auth)])
    def update_user(user_id: str, body: UserUpdate, actor: str = Depends(_require_admin)):
        try:
            user = users.update(
                user_id,
                display_name=body.display_name,
                email=body.email,
                role=body.role,
                status=body.status,
            )
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        except UserConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except UserError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return user.public_dict()

    @router.delete("/{user_id}", status_code=204, dependencies=[Depends(combined_auth)])
    def delete_user(user_id: str, actor: str = Depends(_require_admin)):
        try:
            users.require(user_id)
            users.delete(user_id)
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        except UserConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        return None

    @router.post("/{user_id}/password", status_code=204, dependencies=[Depends(combined_auth)])
    def set_password(user_id: str, body: PasswordUpdate, actor: str = Depends(_require_admin)):
        try:
            users.set_password(user_id, body.password)
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        except UserError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return None

    @router.get("/{user_id}/workspaces", dependencies=[Depends(combined_auth)])
    def get_workspaces(user_id: str, actor: str = Depends(_require_admin)):
        try:
            return {"workspaces": users.require(user_id).workspaces}
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.put("/{user_id}/workspaces", dependencies=[Depends(combined_auth)])
    def set_workspaces(user_id: str, body: WorkspaceGrants, actor: str = Depends(_require_admin)):
        try:
            user = users.set_workspaces(user_id, body.workspaces, granted_by=actor)
        except UserNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"workspaces": user.workspaces}

    # The "an install always keeps one active administrator" guard used to live
    # here, as two functions. It now lives in `UserService`, because a rule that
    # prevents an irreversible lockout has to hold on every surface: enforced in
    # this adapter, it protected callers arriving over HTTP and left the local
    # console — the one an operator reaches for *after* being locked out — free
    # to cause the exact lockout it guards against. The router's remaining job is
    # to map the service's `UserConflict` onto 409.

    return router
