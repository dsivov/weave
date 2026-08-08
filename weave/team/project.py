"""The project a workspace's developers work on (P8).

A dev host is generic; a *project* is what makes it useful. This record answers
the three questions a fresh container has and cannot guess: **which repository**,
**which branch do task branches start from**, and **what command proves the work
is good**.

**Whose answer it is.** The project belongs to the team, not to whoever installed
the daemon. A manager or architect sets it once in Weave, and every machine that
registers into the workspace picks it up on its next heartbeat. So onboarding a
new box is `weave-devhost --server … --workspace …` and nothing else: it learns
the repo from the graph. Changing the base branch changes it fleet-wide, without
anyone touching a machine.

**What is deliberately not here.** No git credentials. The daemon clones on the
*host*, using whatever credentials that machine already has, and bind-mounts a
worktree into each container — so a credential never enters a container that is
about to run an agent with full write permission. And no commit policy: the
worker owns git (branch, commit, PR) precisely so the artifact chain in Weave
matches what is in the repository, and the agent inside is told not to touch it.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from weave_core.utils import logger

from weave_core.store.record import (
    InMemoryRecordStore, JsonRecordStore, RecordStore,
)

# One project per workspace, so the record has a fixed id.
PROJECT_ID = "project"

DEFAULT_TEST_COMMAND = ["python", "-m", "pytest", "-q"]


@dataclass
class WeaveProject:
    """What every developer on this workspace is working on."""

    id: str = PROJECT_ID
    repo: str = ""                           # clone URL or path, host-resolvable
    base_branch: str = "main"                # what each task branch starts from
    image: str = ""                          # container image developers run in
    test_command: List[str] = field(default_factory=lambda: list(DEFAULT_TEST_COMMAND))
    setup_command: List[str] = field(default_factory=list)   # deps, once per worktree
    description: str = ""
    updated_at: float = 0.0
    updated_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WeaveProject":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def onboarding(self) -> Dict[str, Any]:
        """The subset a machine needs to start work — what rides on a heartbeat."""
        return {"repo": self.repo, "base_branch": self.base_branch,
                "image": self.image, "test_command": list(self.test_command),
                "setup_command": list(self.setup_command)}


class WeaveProjectStore(RecordStore[WeaveProject]):
    record_type = WeaveProject


class InMemoryWeaveProjectStore(InMemoryRecordStore[WeaveProject], WeaveProjectStore):
    pass


class JsonWeaveProjectStore(JsonRecordStore[WeaveProject], WeaveProjectStore):
    filename_prefix = "weave_project"


class ProjectService:
    """Read/write the workspace's project definition."""

    def __init__(self, store: WeaveProjectStore, *,
                 now: Callable[[], float] = time.time) -> None:
        self._store = store
        self._now = now

    @property
    def store(self) -> WeaveProjectStore:
        return self._store

    def get(self, workspace: str) -> WeaveProject:
        """The project, or an empty one. Never ``None``: a host asking "what am I
        working on?" before anybody configured it should get a well-formed answer
        saying "nothing yet", not an error it has to special-case."""
        return self._store.get(workspace, PROJECT_ID) or WeaveProject()

    def set(self, workspace: str, *, repo: Optional[str] = None,
            base_branch: Optional[str] = None, image: Optional[str] = None,
            test_command: Optional[List[str]] = None,
            setup_command: Optional[List[str]] = None,
            description: Optional[str] = None, by: str = "") -> WeaveProject:
        """Update the project. Fields left as ``None`` keep their current value,
        so setting the image doesn't silently reset the test command."""
        p = self.get(workspace)
        if repo is not None:
            p.repo = repo.strip()
        if base_branch is not None:
            p.base_branch = base_branch.strip() or "main"
        if image is not None:
            p.image = image.strip()
        if test_command is not None:
            p.test_command = list(test_command) or list(DEFAULT_TEST_COMMAND)
        if setup_command is not None:
            p.setup_command = list(setup_command)
        if description is not None:
            p.description = description
        p.updated_at, p.updated_by = self._now(), by
        self._store.save(workspace, p)
        logger.info(f"Weave: project for '{workspace}' set to {p.repo or '(no repo)'}"
                    f" @ {p.base_branch}")
        return p
