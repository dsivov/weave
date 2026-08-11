"""The live surface over HTTP — SSE and presence.

Endpoints (workspace-scoped via the ``WEAVE-WORKSPACE`` header):

  GET  /live/stream    — Server-Sent Events, filtered to the caller's workspace
  POST /live/presence  — heartbeat: who is on a board, what they are editing
  GET  /live/presence  — who else is here

A thin adapter, like `/ask` and `/projects`. The filtering rules live in
`weave/live/stream.py` where they can be tested without HTTP; this module maps
them onto a response and a status code.

**The identity is the authenticated principal, never the request body** (A6). A
client that could name itself could appear on a colleague's board as that
colleague — a small lie with a large blast radius in a system where the board is
how people decide what to pick up.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from weave.live.presence import PRESENCE_EVENT, Presence, PresenceRegistry
from weave.live.stream import EventStream, presence_event
from weave.server.utils import authenticated_principal, get_combined_auth_dependency


class PresenceUpdate(BaseModel):
    board: str = Field("", description="The board this client is looking at.")
    editing: str = Field("", description="The artifact id this client has open.")
    # Deliberately no `user` field. See the module docstring.


def create_live_routes(
    bus,
    presence: PresenceRegistry,
    *,
    api_key: Optional[str] = None,
    workspace_resolver=None,
    membership=None,
):
    """Build the /live router.

    `membership(user, workspace) -> bool` is re-consulted on every event rather
    than once at connect time: a stream outlives a revocation, and membership
    removed while someone holds one open must stop it. Otherwise revocation would
    mean "applies at the next page load".
    """
    if workspace_resolver is None:
        from weave.server.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(prefix="/live", tags=["live"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _principal(request: Request) -> Dict[str, Any]:
        return authenticated_principal(request)

    def _user_of(principal: Dict[str, Any]) -> str:
        return str(principal.get("sub") or principal.get("username") or "")

    def _may_access(user: str, workspace: str) -> bool:
        return True if membership is None else bool(membership(user, workspace))

    # Subscribe presence to the bus once, so a change on any worker reaches this
    # process's registry. Presence is per-process state; the bus is what makes it
    # look like one shared board (A7).
    async def _absorb_presence(event) -> None:
        if event.type == PRESENCE_EVENT:
            presence.apply(Presence.from_dict(event.payload))

    bus.subscribe(PRESENCE_EVENT, _absorb_presence)

    @router.get("/stream", dependencies=[Depends(combined_auth)],
                summary="Server-Sent Events for this workspace")
    async def stream(request: Request):
        workspace = _ws()
        user = _user_of(_principal(request))
        if not _may_access(user, workspace):
            raise HTTPException(status_code=403, detail="Not a member of this workspace.")

        events = EventStream(
            workspace, may_access=lambda ws: _may_access(user, ws)
        )
        events.subscribe_to(bus)

        return StreamingResponse(
            events.frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Nginx buffers text/event-stream by default, which turns a live
                # board into a batch one and looks exactly like a dead stream.
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/presence", status_code=204,
                 dependencies=[Depends(combined_auth)],
                 summary="Heartbeat: this client is here, editing this")
    async def heartbeat(body: PresenceUpdate, request: Request):
        workspace = _ws()
        principal = _principal(request)
        user = _user_of(principal)
        if not user:
            raise HTTPException(
                status_code=401, detail="Presence requires an authenticated identity."
            )
        if not _may_access(user, workspace):
            raise HTTPException(status_code=403, detail="Not a member of this workspace.")

        entry = presence.touch(
            workspace, user, board=body.board, editing=body.editing,
            role=str(principal.get("role") or ""),
        )
        # Published, not just stored: every other worker's boards learn about
        # this client through the bus.
        await bus.publish(presence_event(entry))
        return None

    @router.get("/presence", dependencies=[Depends(combined_auth)],
                summary="Who else is on this board")
    async def who(request: Request, board: str = ""):
        workspace = _ws()
        user = _user_of(_principal(request))
        if not _may_access(user, workspace):
            raise HTTPException(status_code=403, detail="Not a member of this workspace.")

        people = presence.on_board(workspace, board)
        return {"workspace": workspace, "board": board or None,
                "present": [p.to_dict() for p in people], "count": len(people)}

    return router
