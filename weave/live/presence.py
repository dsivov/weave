"""Presence — who is on a board, and what they are editing.

Deliberately **not durable**. Presence that outlives the person is worse than no
presence: a board showing three people editing an artifact none of them has had
open for an hour teaches its readers to ignore it. So entries expire on a TTL and
a stale entry is simply absent rather than shown greyed-out and hopeful.

Kept in memory per process on purpose, which is a real limitation and is stated
rather than hidden: behind several workers, each worker knows only its own
clients. The fix is the same one A7 already requires — presence changes are
published on the event bus, so every worker learns about every client through the
bus rather than through shared state. The map here is a cache of what the bus has
said, not a source of truth.

The identity is taken from the authenticated principal, never from the request
body (A6). A client that could name itself could impersonate a colleague on the
board, which is a small lie with a large blast radius in a system where the board
is how people decide what to pick up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: How long a presence entry survives without a heartbeat. Longer than the UI's
#: heartbeat interval by enough to ride out one missed beat, short enough that a
#: closed laptop disappears while someone is still looking at the board.
PRESENCE_TTL = 45.0

#: The event type presence changes are published under.
PRESENCE_EVENT = "live.presence"


@dataclass
class Presence:
    """One person, on one board, at one moment."""

    user: str
    workspace: str
    board: str = ""
    editing: str = ""          # the artifact id they have open, if any
    role: str = ""
    at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user": self.user,
            "workspace": self.workspace,
            "board": self.board,
            "editing": self.editing,
            "role": self.role,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Presence":
        return cls(
            user=str(d.get("user") or ""),
            workspace=str(d.get("workspace") or ""),
            board=str(d.get("board") or ""),
            editing=str(d.get("editing") or ""),
            role=str(d.get("role") or ""),
            at=float(d.get("at") or 0.0),
        )


class PresenceRegistry:
    """Who is where, right now, per workspace.

    Expiry is evaluated on read rather than swept on a timer: a background sweep
    is state and concurrency where none is needed, and a reader that filters is
    correct even if nothing has touched the registry for an hour.
    """

    def __init__(self, *, ttl: float = PRESENCE_TTL, now=time.time) -> None:
        self._ttl = ttl
        self._now = now
        self._by_workspace: Dict[str, Dict[str, Presence]] = {}

    def touch(
        self,
        workspace: str,
        user: str,
        *,
        board: str = "",
        editing: str = "",
        role: str = "",
    ) -> Presence:
        """Record that *user* is present. The identity is the caller's, not theirs
        to choose — the router passes the authenticated principal."""
        entry = Presence(
            user=user, workspace=workspace, board=board, editing=editing,
            role=role, at=self._now(),
        )
        self._by_workspace.setdefault(workspace, {})[user] = entry
        return entry

    def leave(self, workspace: str, user: str) -> bool:
        return self._by_workspace.get(workspace, {}).pop(user, None) is not None

    def on_board(self, workspace: str, board: str = "") -> List[Presence]:
        """Everyone currently present, most recently seen first.

        Filtered by workspace at the source: presence is as tenant-scoped as
        anything else, and "who else is here" leaking across workspaces would
        expose colleagues, boards and artifact ids to another tenant.
        """
        cutoff = self._now() - self._ttl
        live = [
            entry
            for entry in self._by_workspace.get(workspace, {}).values()
            if entry.at >= cutoff and (not board or entry.board == board)
        ]
        return sorted(live, key=lambda e: e.at, reverse=True)

    def editing(self, workspace: str, artifact: str) -> List[Presence]:
        """Who has *artifact* open — the question a 409 wants answered."""
        return [e for e in self.on_board(workspace) if e.editing == artifact]

    def apply(self, entry: Presence) -> None:
        """Absorb a presence entry learned from the bus (another worker's client).

        Older than what we already hold is ignored, so events arriving out of
        order cannot resurrect a stale position.
        """
        current = self._by_workspace.get(entry.workspace, {}).get(entry.user)
        if current is not None and current.at >= entry.at:
            return
        self._by_workspace.setdefault(entry.workspace, {})[entry.user] = entry

    def snapshot(self, workspace: str) -> Dict[str, Any]:
        people = self.on_board(workspace)
        return {
            "workspace": workspace,
            "present": [p.to_dict() for p in people],
            "count": len(people),
        }
