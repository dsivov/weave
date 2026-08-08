"""Workspace-scoped record stores — the shape every Weave registry persists with.

A registry (workers, dev hosts) keeps one small dataclass per id per workspace,
and needs exactly two backends: in-memory for tests and a single JSON file per
workspace for a running server. That storage shape has nothing to do with what
is being stored, so it lives here once and is parameterised by the record type.

The record only has to satisfy :class:`Record`: ``id``, ``to_dict()``, and a
``from_dict()`` classmethod. Everything else — atomic replace, tolerating a
corrupt file rather than crashing a server on boot, workspace name sanitising —
is the same for every registry and is implemented once.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, List, Optional, Protocol, Type, TypeVar

from weave_core.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class Record(Protocol):
    id: str

    def to_dict(self) -> Dict[str, Any]: ...

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Any: ...


R = TypeVar("R", bound=Record)


class RecordStore(ABC, Generic[R]):
    """Typed CRUD over whatever the concrete backend persists."""

    record_type: Type[R]

    def save(self, workspace: str, record: R) -> None:
        self._write(workspace, record.id, record.to_dict())

    def get(self, workspace: str, record_id: str) -> Optional[R]:
        d = self._read(workspace, record_id)
        return self.record_type.from_dict(d) if d is not None else None

    def list(self, workspace: str) -> List[R]:
        return [self.record_type.from_dict(d) for d in self._all(workspace)]

    def delete(self, workspace: str, record_id: str) -> bool:
        return self._delete(workspace, record_id)

    @abstractmethod
    def _write(self, ws: str, rid: str, d: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _read(self, ws: str, rid: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _all(self, ws: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def _delete(self, ws: str, rid: str) -> bool: ...


class InMemoryRecordStore(RecordStore[R]):
    def __init__(self) -> None:
        self._d: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _ws(self, ws: str) -> Dict[str, Dict[str, Any]]:
        return self._d.setdefault(ws, {})

    def _write(self, ws, rid, d):
        self._ws(ws)[rid] = dict(d)

    def _read(self, ws, rid):
        d = self._ws(ws).get(rid)
        return dict(d) if d else None

    def _all(self, ws):
        return [dict(d) for d in self._ws(ws).values()]

    def _delete(self, ws, rid):
        return self._ws(ws).pop(rid, None) is not None


class JsonRecordStore(RecordStore[R]):
    """One JSON file per workspace, replaced atomically.

    ``filename_prefix`` names the file (``<prefix>_<workspace>.json``), so two
    registries sharing a directory never collide.
    """

    filename_prefix: str = "weave_records"

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"{self.filename_prefix}_{name}.json")

    def _load(self, ws: str) -> Dict[str, Dict[str, Any]]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            # A damaged file must not stop a server booting: the fleet re-registers
            # on its next heartbeat anyway.
            logger.warning(f"{type(self).__name__} could not read {p}: {e}")
            return {}

    def _dump(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _write(self, ws, rid, d):
        data = self._load(ws)
        data[rid] = d
        self._dump(ws, data)

    def _read(self, ws, rid):
        return self._load(ws).get(rid)

    def _all(self, ws):
        return list(self._load(ws).values())

    def _delete(self, ws, rid):
        data = self._load(ws)
        if rid in data:
            del data[rid]
            self._dump(ws, data)
            return True
        return False
