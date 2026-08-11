"""`ProjectLayout` — the registry that turns a locator back into a real document.

An index whose entries do not lead back to the document is trivia. A locator
names a repository, a path and a revision; this registry says what that
repository *is* on this installation — where to clone it from, where a checkout
lives on the server, and what revision to assume when a locator does not say.

**It is workspace-scoped, and that is a security property, not tidiness.**
`resolve()` returns file content. A global registry would let any tenant read any
other tenant's repository through a locator they made up, which inverts the
guarantee per-workspace membership exists to give (R22a, A14, D-028). So the
workspace is the first argument of every call — required by signature, not by
convention — and a repository genuinely shared by several workspaces is
registered in each (R22b). There is no cross-workspace registration.

**A repository not registered in the caller's workspace does not resolve, and
the caller cannot tell why.** `NotRegistered` carries no hint about whether the
repository exists somewhere else, because "no such repository here" and "not
yours" must be indistinguishable — otherwise the error message becomes an
enumeration oracle for other tenants' repositories.

**Resolution is against the recorded revision, never `HEAD`** (R23). A file that
moved last week still resolves for the artifact that cited it, because the
artifact cited a commit. That is why `git show <rev>:<path>` is the read, rather
than opening the file from the working tree — the working tree is whatever the
checkout happens to be on, which is a moving target and usually the wrong one.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from weave.model.locator import Locator
from weave_core.store.record import (
    InMemoryRecordStore,
    JsonRecordStore,
    RecordStore,
)
from weave_core.utils import logger

#: A repository name: what a locator's `repo` field holds. Constrained because it
#: becomes a record id and, through `local_path`, part of a filesystem read.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: How long a `git show` may take before we give up. A hung resolve on a board
#: render would otherwise take the request thread with it.
_GIT_TIMEOUT = 20.0

#: Refuse to hand back an object this large as `content`. A resolver answers
#: "what does this artifact point at"; streaming a 50 MB blob through it is a
#: different feature with different needs.
MAX_CONTENT_BYTES = 2 * 1024 * 1024


class ProjectLayoutError(ValueError):
    """A registration that cannot be stored, or a resolve that cannot proceed."""


class NotRegistered(ProjectLayoutError):
    """The repository is not registered **in this workspace**.

    Deliberately says nothing about whether it exists elsewhere. The router maps
    this to a bare 404 for the same reason (R22a).
    """


@dataclass
class ProjectLayout:
    """One repository, as this installation knows it.

    ``name`` is what a locator's ``repo`` field holds, and it is the record id —
    a workspace registers one layout per repository name.
    """

    name: str
    clone_url: str = ""
    local_path: str = ""
    default_rev: str = "main"
    description: str = ""

    @property
    def id(self) -> str:
        return self.name

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProjectLayout":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def public_dict(self) -> Dict[str, Any]:
        """What an API returns. ``local_path`` is a server filesystem path and is
        not part of the answer to "what repositories can I see"."""
        return {
            "name": self.name,
            "clone_url": self.clone_url,
            "default_rev": self.default_rev,
            "description": self.description,
            "has_local_checkout": bool(self.local_path),
        }


class ProjectLayoutStore(RecordStore[ProjectLayout]):
    record_type = ProjectLayout


class InMemoryProjectLayoutStore(InMemoryRecordStore[ProjectLayout], ProjectLayoutStore):
    pass


class JsonProjectLayoutStore(JsonRecordStore[ProjectLayout], ProjectLayoutStore):
    filename_prefix = "weave_projects"


# ── URL construction ─────────────────────────────────────────────────────────


#: `scheme://[user@]host/path`. The scheme is captured rather than assumed,
#: because only three of them describe something browsable.
_WITH_SCHEME = re.compile(r"^(?P<scheme>[A-Za-z][\w+.-]*)://(?P<rest>.*)$")

#: `[user@]host:path` — the scp-like form git accepts with no scheme at all.
#: The negative lookahead keeps `host:2222/path` (a port) from being read as a
#: path, and requiring a dot in the host keeps `C:/checkout` out.
_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w-]+(?:\.[\w-]+)+):(?!\d)(?P<path>.+)$")

#: Schemes that identify a host serving a web UI. `file://` and `git://` are
#: deliberately absent: both are perfectly good clone URLs and neither implies
#: anything a browser can open.
_BROWSABLE_SCHEMES = {"https", "http", "ssh"}


def browse_url(clone_url: str, path: str, rev: str, anchor: Optional[str] = None) -> str:
    """A URL a human can click, derived from the clone URL.

    Handles the forms a clone URL actually takes — `https://host/org/repo`,
    `git@host:org/repo.git` and `ssh://git@host/org/repo.git` — and lays out the
    common hosting convention (`/blob/<rev>/<path>`).

    Anything else returns the empty string rather than a guess. A wrong link is
    worse than no link: a reader follows it, gets a 404 or somebody else's file,
    and draws a conclusion either way. `file://` and `git://` are the ordinary
    cases here — both are valid clone URLs and neither implies a web UI.
    """
    clone_url = (clone_url or "").strip()
    if not clone_url:
        return ""

    scheme_match = _WITH_SCHEME.match(clone_url)
    if scheme_match:
        scheme = scheme_match.group("scheme").lower()
        if scheme not in _BROWSABLE_SCHEMES:
            return ""
        rest = scheme_match.group("rest")
        _, _, after_user = rest.rpartition("@")
        host, _, repo_path = (after_user or rest).partition("/")
        # `ssh://git@host:2222/org/repo` — the port is not part of the web host.
        host = host.split(":", 1)[0]
        # An `ssh` clone URL says nothing about the web scheme, so https is the
        # only reasonable assumption. An `http` one does say, and upgrading it
        # would be inventing a link to a host that may not serve TLS.
        web_scheme = "https" if scheme == "ssh" else scheme
    else:
        scp_match = _SCP_LIKE.match(clone_url)
        if not scp_match:
            return ""
        host, repo_path = scp_match.group("host"), scp_match.group("path")
        web_scheme = "https"

    repo_path = repo_path.strip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[: -len(".git")]
    if not host or not repo_path:
        return ""

    url = f"{web_scheme}://{host}/{repo_path}/blob/{rev}/{path.lstrip('/')}"
    return f"{url}#{anchor}" if anchor else url


# ── the registry ─────────────────────────────────────────────────────────────


class ProjectLayoutRegistry:
    """Register repositories in a workspace, and resolve locators within it.

    Every method takes the workspace first. That is the whole design: the
    signature makes it impossible to ask "resolve this locator" without saying
    on whose behalf.
    """

    def __init__(self, store: ProjectLayoutStore, *, runner=None) -> None:
        self._store = store
        # Injectable so the resolver's git interaction can be exercised without
        # a repository, and so a test can assert *which* revision was asked for.
        self._run_git = runner or _run_git

    # -- registration -------------------------------------------------------

    def register(
        self,
        workspace: str,
        name: str,
        *,
        clone_url: str = "",
        local_path: str = "",
        default_rev: str = "main",
        description: str = "",
    ) -> ProjectLayout:
        name = (name or "").strip()
        if not _NAME_RE.match(name):
            raise ProjectLayoutError(
                f"'{name}' is not a usable repository name; allowed characters "
                "are letters, digits, dot, dash and underscore."
            )
        if not (clone_url.strip() or local_path.strip()):
            raise ProjectLayoutError(
                f"'{name}' needs a clone_url (so a human can read it) or a "
                "local_path (so an agent can), and preferably both."
            )
        layout = ProjectLayout(
            name=name,
            clone_url=clone_url.strip(),
            local_path=local_path.strip(),
            default_rev=(default_rev or "main").strip(),
            description=description.strip(),
        )
        self._store.save(workspace, layout)
        logger.info(f"project layout: registered '{name}' in workspace '{workspace}'")
        return layout

    def list(self, workspace: str) -> List[ProjectLayout]:
        return sorted(self._store.list(workspace), key=lambda p: p.name.lower())

    def get(self, workspace: str, name: str) -> Optional[ProjectLayout]:
        return self._store.get(workspace, name)

    def require(self, workspace: str, name: str) -> ProjectLayout:
        layout = self.get(workspace, name)
        if layout is None:
            # No mention of other workspaces, deliberately (R22a).
            raise NotRegistered(f"No repository '{name}' is registered here.")
        return layout

    def unregister(self, workspace: str, name: str) -> bool:
        return self._store.delete(workspace, name)

    # -- resolution ---------------------------------------------------------

    def resolve(
        self, workspace: str, locator: Locator, *, want_content: bool = True
    ) -> Dict[str, Any]:
        """Resolve *locator* within *workspace*.

        Returns `{repo, path, rev, url, exists, content?}`. `exists` is the
        honest answer to "is this pointer still good", which is what the resolver
        check (R24) counts and what the M2 gate requires to be zero-dangling.

        Raises :class:`NotRegistered` when the repository is not registered in
        this workspace — never a partial answer, and never a different error for
        "exists but is someone else's".
        """
        layout = self.require(workspace, locator.repo)
        rev = locator.rev or layout.default_rev

        result: Dict[str, Any] = {
            "repo": locator.repo,
            "path": locator.path,
            "rev": rev,
            "url": browse_url(layout.clone_url, locator.path, rev, locator.anchor),
            "exists": False,
        }
        if locator.anchor:
            result["anchor"] = locator.anchor

        if not layout.local_path:
            # Registered for humans only: we can produce a link but cannot claim
            # the file is there. `exists` stays False rather than guessing.
            result["reason"] = (
                f"'{locator.repo}' has no server-side checkout, so its content "
                "cannot be read here."
            )
            return result

        blob = self._read_blob(layout, locator.path, rev)
        if blob.error:
            result["reason"] = blob.error
            return result

        result["exists"] = True
        result["size"] = blob.size
        if want_content:
            if blob.size > MAX_CONTENT_BYTES:
                result["truncated"] = True
                result["reason"] = (
                    f"{blob.size} bytes exceeds the {MAX_CONTENT_BYTES}-byte "
                    "resolve limit; fetch it from the repository directly."
                )
            elif blob.text is None:
                result["reason"] = "binary content; use the url"
            else:
                result["content"] = blob.text
        return result

    def _read_blob(self, layout: ProjectLayout, path: str, rev: str) -> "_Blob":
        """Read `path` at `rev` out of the registered checkout.

        `git show <rev>:<path>` rather than opening the file, because the
        checkout's working tree is on whatever revision it happens to be on and
        the locator is a claim about a specific one (R23).
        """
        if not os.path.isdir(layout.local_path):
            return _Blob(error=f"the registered checkout for '{layout.name}' is missing")
        return self._run_git(layout.local_path, rev, path.lstrip("/"))


@dataclass
class _Blob:
    text: Optional[str] = None
    size: int = 0
    error: str = ""


def _run_git(local_path: str, rev: str, path: str) -> _Blob:
    try:
        proc = subprocess.run(
            ["git", "show", f"{rev}:{path}"],
            cwd=local_path,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError:
        return _Blob(error="git is not available on the server")
    except subprocess.TimeoutExpired:
        return _Blob(error=f"reading {path} at {rev} timed out")

    if proc.returncode != 0:
        # git's own message names whether the path or the revision was wrong,
        # which is the distinction someone fixing a dangling locator needs.
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return _Blob(error=detail[-1] if detail else f"{path} not found at {rev}")

    raw = proc.stdout
    try:
        return _Blob(text=raw.decode("utf-8"), size=len(raw))
    except UnicodeDecodeError:
        return _Blob(text=None, size=len(raw))
