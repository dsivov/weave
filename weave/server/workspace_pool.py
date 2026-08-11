"""Workspace pool for multi-tenant WeaveEngine/WeaveGraph instances.

Each workspace gets its own fully initialized WeaveGraph/WeaveEngine instance
with isolated storage (Neo4j labels, KV namespaces, vector collections).
Instances are created lazily on first request and cached.

Uses a proxy pattern with contextvars so existing route code needs zero changes —
the ``rag`` object passed to routes transparently delegates to the correct
workspace instance based on the current request's ``WEAVE-WORKSPACE`` header.
"""

import asyncio
import contextvars
import re
from typing import Type

from weave_core.utils import logger

# Valid workspace name: alphanumeric and underscores only
_WORKSPACE_RE = re.compile(r"^[a-zA-Z0-9_]+$")

#: The header that selects the tenant. **This is the one place it is named.**
#:
#: It is a published contract — the OpenAPI document, the UI client, the dev
#: worker and the playbook all send or document this exact string — so the
#: middleware must not carry its own copy of it. It did, and the copy was wrong:
#: a rebrand renamed the middleware's literal to a name no client has ever sent,
#: and because a raw ASGI lookup that misses returns the default rather than an
#: error, every request in the system resolved to the default workspace with no
#: log line to show for it.
#:
#: The general shape (the reason this is a constant and not a corrected literal):
#: a renamed literal is safe when both sides of the comparison were renamed
#: together, and broken when the other side is produced outside this codebase.
#: Header names are the second kind, so there is exactly one of this string.
WORKSPACE_HEADER = "WEAVE-WORKSPACE"

#: ASGI delivers raw header names **lowercased** in ``scope["headers"]``, so a
#: raw-scope read must compare against the lowercase form. Derived, never typed
#: out again — ``b"WEAVE-WORKSPACE"`` would be just as dead as the name it
#: replaced, and would fail exactly as quietly.
_WORKSPACE_HEADER_BYTES = WORKSPACE_HEADER.lower().encode("latin-1")

# Context variable holding the current workspace name for the active request
_current_workspace: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_workspace", default="default"
)


class WorkspacePool:
    """Manages a pool of WeaveEngine/WeaveGraph instances, one per workspace."""

    def __init__(self, rag_cls: Type, rag_kwargs: dict, post_create=None,
                 admission_probe=None):
        self._rag_cls = rag_cls
        self._rag_kwargs = rag_kwargs
        # Which graph backend this deployment runs on. Some hold exactly one
        # workspace (A4 v4, D-029) and a second must be refused *here*, at the
        # point a workspace comes into being, rather than documented.
        self._graph_storage = str(rag_kwargs.get("graph_storage") or "")
        # Injectable so the policy can be exercised without a live database.
        self._admission_probe = admission_probe
        # Optional hook run on every freshly-constructed instance (e.g. to attach
        # per-task LLM roles). Called synchronously with the new rag instance.
        self._post_create = post_create
        self._instances: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._needs_init: set[str] = set()

    def _make(self, workspace: str):
        rag = self._rag_cls(workspace=workspace, **self._rag_kwargs)
        if self._post_create is not None:
            self._post_create(rag)
        return rag

    def seed(self, workspace: str):
        """Synchronously create and register an instance (no storage init).

        Used during app setup so the proxy can resolve attributes before
        the async lifespan starts.  The lifespan should call
        ``await finalize_seed(workspace)`` to complete initialization.
        """
        if not workspace:
            workspace = "default"
        if workspace not in self._instances:
            rag = self._make(workspace)
            self._instances[workspace] = rag
            self._needs_init.add(workspace)
            logger.info(f"Seeded workspace: {workspace} (pending async init)")
        return self._instances[workspace]

    async def finalize_seed(self, workspace: str):
        """Complete async initialization for a seeded workspace.

        The seeded default is admitted here rather than in :meth:`seed`, which
        is synchronous and cannot ask the database anything. It matters: a
        deployment whose default workspace is `X`, pointed at a Neo4j that
        already holds `Y`, would otherwise open a second workspace on a
        single-workspace backend at boot — the silent co-tenancy D-029 exists to
        prevent, arriving through the one door that skips the creation check.

        A conflict here stops startup, which is the intended outcome: the
        deployment is misconfigured, and serving it would quietly co-tenant two
        workspaces in one database. An *unreachable* database is a different
        case and does not stop anything — see `workspace_admission`.
        """
        if workspace in self._needs_init:
            await self._check_admission(workspace)
            rag = self._instances[workspace]
            await rag.initialize_storages()
            await rag.check_and_migrate_data()
            self._needs_init.discard(workspace)
            logger.info(f"Workspace '{workspace}' fully initialized")

    async def get_rag(self, workspace: str):
        """Get or create a rag instance for the given workspace."""
        if not workspace:
            workspace = "default"

        if not _WORKSPACE_RE.match(workspace):
            raise ValueError(
                f"Invalid workspace name '{workspace}'. "
                "Only a-z, A-Z, 0-9, and _ are allowed."
            )

        # Fast path: instance already exists
        if workspace in self._instances:
            # May still need async init if seeded
            if workspace in self._needs_init:
                await self.finalize_seed(workspace)
            return self._instances[workspace]

        # Slow path with per-workspace lock
        async with self._global_lock:
            if workspace not in self._locks:
                self._locks[workspace] = asyncio.Lock()
            lock = self._locks[workspace]

        async with lock:
            if workspace in self._instances:
                return self._instances[workspace]

            # The creation point. Everything above this line is a lookup of a
            # workspace that already exists; below it, one comes into being —
            # which is exactly where a single-workspace backend has to say no.
            await self._check_admission(workspace)

            logger.info(f"Initializing workspace: {workspace}")
            rag = self._make(workspace)
            await rag.initialize_storages()
            await rag.check_and_migrate_data()
            self._instances[workspace] = rag
            logger.info(f"Workspace '{workspace}' ready")
            return rag

    async def _check_admission(self, workspace: str) -> None:
        """Refuse a workspace this deployment's graph backend cannot hold.

        Delegated to `weave.server.workspace_admission`, which owns the policy
        and asks the adapter what the database already holds — so the refusal
        survives a restart rather than resting on this process's dictionary.
        """
        from weave.server.workspace_admission import check_admission

        await check_admission(
            workspace,
            self._graph_storage,
            # The *other* workspaces this process holds. The candidate is
            # excluded deliberately: `seed()` registers an instance before
            # `finalize_seed()` admits it, so counting it would make the seeded
            # default admit itself and the boot-time check would be theatre.
            known_workspaces=[w for w in self._instances if w != workspace],
            probe=self._admission_probe,
        )

    @property
    def workspaces(self) -> list[str]:
        return list(self._instances.keys())

    async def shutdown(self):
        for workspace, rag in self._instances.items():
            logger.info(f"Shutting down workspace: {workspace}")
            try:
                await rag.finalize_storages()
            except Exception as e:
                logger.error(f"Error finalizing workspace '{workspace}': {e}")
        self._instances.clear()
        self._locks.clear()


class WorkspaceProxy:
    """Proxy that delegates attribute access to the workspace-specific rag instance.

    Routes receive this object as ``rag``.  On every attribute access it
    looks up the current workspace (set by middleware via contextvars) and
    forwards the call to the real WeaveGraph/WeaveEngine instance from the pool.
    """

    def __init__(self, pool: WorkspacePool):
        # Use object.__setattr__ to avoid triggering __setattr__ proxy
        object.__setattr__(self, "_pool", pool)

    def _get_current_rag(self):
        """Synchronously return the cached rag for the current workspace.

        Raises RuntimeError if the workspace hasn't been initialized yet
        (middleware should have done this).
        """
        workspace = _current_workspace.get()
        pool: WorkspacePool = object.__getattribute__(self, "_pool")
        if workspace in pool._instances:
            return pool._instances[workspace]
        raise RuntimeError(
            f"Workspace '{workspace}' not initialized. "
            "The workspace middleware should have called pool.get_rag() first."
        )

    def __getattr__(self, name):
        return getattr(self._get_current_rag(), name)

    def __setattr__(self, name, value):
        setattr(self._get_current_rag(), name, value)

    # Support isinstance() checks used by weave.server.routers.reasoning._require_quadruple()
    def __class_getitem__(cls, item):
        return cls

    @property
    def __class__(self):
        """Report as the class of the underlying rag instance.

        This makes ``isinstance(proxy, WeaveGraph)`` work correctly.
        """
        return type(self._get_current_rag())


def get_workspace_middleware(pool: WorkspacePool, default_workspace: str = "default"):
    """Return a **pure ASGI** middleware that sets the workspace context per request.

    Deliberately not a ``BaseHTTPMiddleware``: that wraps the ASGI receive channel
    and corrupts request-body streaming for mounted sub-apps (notably the MCP
    Streamable-HTTP transport — large bodies would truncate at a chunk boundary).
    This one reads only the ``WEAVE-WORKSPACE`` header from ``scope`` and never
    touches ``receive``/``send``, so the body streams through untouched.
    """

    from starlette.responses import JSONResponse

    from weave.server.workspace_admission import WorkspaceNotAdmitted

    class WorkspaceMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            headers = dict(scope.get("headers") or [])
            workspace = (
                headers.get(_WORKSPACE_HEADER_BYTES, b"").decode("latin-1").strip()
            )
            if not workspace:
                workspace = default_workspace

            if not _WORKSPACE_RE.match(workspace):
                resp = JSONResponse(
                    status_code=400,
                    content={"detail": f"Invalid workspace name '{workspace}'. "
                             "Only a-z, A-Z, 0-9, and _ are allowed."})
                await resp(scope, receive, send)
                return

            try:
                await pool.get_rag(workspace)
            except WorkspaceNotAdmitted as e:
                # Not a failure to initialise: a deliberate refusal by policy
                # (A4 v4, D-029). 409, and the message says which workspace
                # holds the backend and what to move to — an operator who hits
                # this needs to act, not to retry.
                logger.warning(f"refused workspace '{workspace}': {e}")
                resp = JSONResponse(status_code=409, content={"detail": str(e)})
                await resp(scope, receive, send)
                return
            except Exception as e:
                logger.error(f"Failed to initialize workspace '{workspace}': {e}")
                resp = JSONResponse(
                    status_code=500,
                    content={"detail": f"Failed to initialize workspace: {e}"})
                await resp(scope, receive, send)
                return

            token = _current_workspace.set(workspace)
            try:
                await self.app(scope, receive, send)
            finally:
                _current_workspace.reset(token)

    return WorkspaceMiddleware
