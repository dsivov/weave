"""The team layer — how work is claimed, run and merged.

**Exports are resolved lazily, and that is load-bearing.** The developer worker
runs in a container holding a Claude Code CLI, git and the standard library —
deliberately not the server's dependency tree. Importing these names eagerly
would drag the coordinator and the preset (and through them ``weave_core`` and
``httpx``) into ``python -m weave.team.worker``, so the container could not start
the one thing it exists to run. Each name below is fetched from its module on
first use instead, which keeps the worker's "stdlib-only" promise true rather
than aspirational.
"""

from typing import TYPE_CHECKING

# name -> the submodule that defines it
_EXPORTS = {
    "PLANNER_ROLES": "coordinator",
    "WeaveCoordinator": "coordinator",
    "WeaveConflict": "coordinator",
    "WeaveForbidden": "coordinator",
    "WeaveNotFound": "coordinator",
    "WeaveTask": "store",
    "WeaveTaskStore": "store",
    "InMemoryWeaveTaskStore": "store",
    "JsonWeaveTaskStore": "store",
    "WorkerRegistry": "workers",
    "WeaveWorker": "workers",
    "WeaveWorkerStore": "workers",
    "InMemoryWeaveWorkerStore": "workers",
    "JsonWeaveWorkerStore": "workers",
    "run_worker": "worker",
    "WeaveClient": "worker",
    "RunResult": "worker",
    "ClaimConflict": "worker",
    "preflight_subscription_auth": "worker",
    "scrub_api_auth": "worker",
    "SubscriptionAuthError": "worker",
    "WeaveProject": "project",
    "WeaveProjectStore": "project",
    "ProjectService": "project",
    "InMemoryWeaveProjectStore": "project",
    "JsonWeaveProjectStore": "project",
    "WeaveEnvironment": "integration",
    "IntegrationRun": "integration",
    "IntegrationStore": "integration",
    "InMemoryIntegrationStore": "integration",
    "JsonIntegrationStore": "integration",
}

# Submodules reachable as attributes (``team.preset``) without an import of their own.
_SUBMODULES = ("playbook", "preset", "coordinator", "store", "workers", "worker",
               "project", "integration")

if TYPE_CHECKING:  # import-time types for checkers, never at runtime
    from weave.team import playbook, preset  # noqa: F401
    from weave.team.coordinator import (  # noqa: F401
        PLANNER_ROLES, WeaveConflict, WeaveCoordinator, WeaveForbidden, WeaveNotFound,
    )
    from weave.team.store import (  # noqa: F401
        InMemoryWeaveTaskStore, JsonWeaveTaskStore, WeaveTask, WeaveTaskStore,
    )
    from weave.team.workers import (  # noqa: F401
        InMemoryWeaveWorkerStore, JsonWeaveWorkerStore, WeaveWorker,
        WeaveWorkerStore, WorkerRegistry,
    )
    from weave.team.worker import (  # noqa: F401
        ClaimConflict, RunResult, SubscriptionAuthError, WeaveClient,
        preflight_subscription_auth, run_worker, scrub_api_auth,
    )
    from weave.team.project import (  # noqa: F401
        InMemoryWeaveProjectStore, JsonWeaveProjectStore, ProjectService,
        WeaveProject, WeaveProjectStore,
    )
    from weave.team.integration import (  # noqa: F401
        InMemoryIntegrationStore, IntegrationRun, IntegrationStore,
        JsonIntegrationStore, WeaveEnvironment,
    )


def __getattr__(name: str):
    import importlib

    if name in _SUBMODULES:
        mod = importlib.import_module(f"weave.team.{name}")
        globals()[name] = mod
        return mod
    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"weave.team.{where}"), name)
    globals()[name] = value          # resolve once; later lookups are direct
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS) | set(_SUBMODULES))


__all__ = [
    "preset", "playbook", "PLANNER_ROLES",
    "WeaveCoordinator", "WeaveConflict", "WeaveForbidden", "WeaveNotFound",
    "WeaveTask", "WeaveTaskStore", "InMemoryWeaveTaskStore", "JsonWeaveTaskStore",
    "WorkerRegistry", "WeaveWorker", "WeaveWorkerStore",
    "InMemoryWeaveWorkerStore", "JsonWeaveWorkerStore",
    "run_worker", "WeaveClient", "RunResult", "ClaimConflict",
    "preflight_subscription_auth", "scrub_api_auth", "SubscriptionAuthError",
    "WeaveProject", "WeaveProjectStore", "ProjectService",
    "InMemoryWeaveProjectStore", "JsonWeaveProjectStore",
    "WeaveEnvironment", "IntegrationRun", "IntegrationStore",
    "InMemoryIntegrationStore", "JsonIntegrationStore",
]
