"""The dev-host bundle — deployable #2 (A1, A15).

One daemon per machine that carries developer agents. It **registers and
heartbeats outbound; nothing ever connects to it** — which is what lets a dev
host sit behind NAT, on a laptop, or in a private VPC. Supervisory acts are state
the host reads back on its next beat (``desired_workers``), never a command
pushed at it.

* :mod:`.registry` — host records, the four control states (``run · drain ·
  pause · stop``) and seat health.
* :mod:`.daemon` — the register → heartbeat → reconcile loop.
* :mod:`.runtime` — the narrow ``ContainerRuntime`` protocol, plus the Docker
  implementation, so reconcile is testable without a Docker daemon.
* :mod:`.worktree` — host-side clone, per-worker worktrees and branch publish, so
  containers hold no git credentials.

Lazy, for the same reason :mod:`weave.team` is: the daemon installs on a
developer machine without the server's dependency set (R75).
"""

from typing import TYPE_CHECKING

_EXPORTS = {
    "DevHost": "registry",
    "DevHostRegistry": "registry",
    "DevHostStore": "registry",
    "HostOwnershipError": "registry",
    "InMemoryDevHostStore": "registry",
    "JsonDevHostStore": "registry",
}

_SUBMODULES = ("registry", "daemon", "runtime", "worktree")

if TYPE_CHECKING:  # noqa
    from weave.devhost.registry import (  # noqa: F401
        DevHost, DevHostRegistry, DevHostStore, HostOwnershipError,
        InMemoryDevHostStore, JsonDevHostStore,
    )


def __getattr__(name: str):
    import importlib

    if name in _SUBMODULES:
        mod = importlib.import_module(f"weave.devhost.{name}")
        globals()[name] = mod
        return mod
    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"weave.devhost.{where}"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS) | set(_SUBMODULES))


__all__ = list(_EXPORTS) + list(_SUBMODULES)
