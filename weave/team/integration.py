"""Integration environment + runs — the merge gate (P4 · M4).

"Run the system" is separated from "write the code". A :class:`WeaveEnvironment`
is the one long-lived shared instance where the assembled frontend + backend + DB
runs; the Integrator owns it. An :class:`IntegrationRun` is a run of integration /
e2e tests against that environment — the **merge gate**, distinct from the dev
container's unit tests (which gate the *PR*). A task reaches ``done`` only when a
run is green.

Weave-native, mirroring the task/worker stores: lean InMemory / Json backends, one
declared environment (or a few) per workspace and an append-only list of runs.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from weave_core.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")

INTEGRATION_KINDS = ("integration", "e2e")


@dataclass
class WeaveEnvironment:
    id: str
    name: str = ""
    url: str = ""
    status: str = "ready"                       # ready · degraded · down
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WeaveEnvironment":
        return cls(id=d["id"], name=d.get("name", ""), url=d.get("url", ""),
                   status=d.get("status", "ready"), config=dict(d.get("config") or {}))


@dataclass
class IntegrationRun:
    id: str
    environment: str
    kind: str = "e2e"                           # integration · e2e
    status: str = "passed"                      # passed · failed
    tasks: List[str] = field(default_factory=list)
    summary: str = ""
    at: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IntegrationRun":
        return cls(id=d["id"], environment=d["environment"], kind=d.get("kind", "e2e"),
                   status=d.get("status", "passed"), tasks=list(d.get("tasks") or []),
                   summary=d.get("summary", ""), at=d.get("at", 0.0))


class IntegrationStore(ABC):
    """Environments (keyed by id) + an append-only run log, per workspace."""

    def save_env(self, ws: str, env: WeaveEnvironment) -> None:
        self._write_env(ws, env.id, env.to_dict())

    def get_env(self, ws: str, env_id: str) -> Optional[WeaveEnvironment]:
        d = self._read_env(ws, env_id)
        return WeaveEnvironment.from_dict(d) if d is not None else None

    def list_envs(self, ws: str) -> List[WeaveEnvironment]:
        return [WeaveEnvironment.from_dict(d) for d in self._all_envs(ws)]

    def add_run(self, ws: str, run: IntegrationRun) -> None:
        self._append_run(ws, run.to_dict())

    def list_runs(self, ws: str) -> List[IntegrationRun]:
        return [IntegrationRun.from_dict(d) for d in self._all_runs(ws)]

    @abstractmethod
    def _write_env(self, ws: str, eid: str, d: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _read_env(self, ws: str, eid: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _all_envs(self, ws: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def _append_run(self, ws: str, d: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _all_runs(self, ws: str) -> List[Dict[str, Any]]: ...


class InMemoryIntegrationStore(IntegrationStore):
    def __init__(self) -> None:
        self._envs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._runs: Dict[str, List[Dict[str, Any]]] = {}

    def _write_env(self, ws, eid, d):
        self._envs.setdefault(ws, {})[eid] = dict(d)

    def _read_env(self, ws, eid):
        d = self._envs.get(ws, {}).get(eid)
        return dict(d) if d else None

    def _all_envs(self, ws):
        return [dict(d) for d in self._envs.get(ws, {}).values()]

    def _append_run(self, ws, d):
        self._runs.setdefault(ws, []).append(dict(d))

    def _all_runs(self, ws):
        return [dict(d) for d in self._runs.get(ws, [])]


class JsonIntegrationStore(IntegrationStore):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"weave_integration_{name}.json")

    def _load(self, ws: str) -> Dict[str, Any]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {"envs": {}, "runs": []}
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
                data.setdefault("envs", {})
                data.setdefault("runs", [])
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"IntegrationStore could not read {p}: {e}")
            return {"envs": {}, "runs": []}

    def _dump(self, ws: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _write_env(self, ws, eid, d):
        data = self._load(ws)
        data["envs"][eid] = d
        self._dump(ws, data)

    def _read_env(self, ws, eid):
        return self._load(ws)["envs"].get(eid)

    def _all_envs(self, ws):
        return list(self._load(ws)["envs"].values())

    def _append_run(self, ws, d):
        data = self._load(ws)
        data["runs"].append(d)
        self._dump(ws, data)

    def _all_runs(self, ws):
        return list(self._load(ws)["runs"])
