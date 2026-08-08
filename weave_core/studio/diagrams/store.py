"""Diagram persistence — versioned, append-only (P6).

Mirrors :class:`~weave_core.flows.store.FlowStore`: raw layout per workspace
is ``{diagram_id: {str(version): diagram_dict}}``, every save assigns the next
version, and older versions stay readable. The Studio ledger records the sign-off
for each applied revision; this store holds the artifacts themselves so a
diagram can be fetched and rendered without walking the ledger.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from weave_core.utils import logger

from weave_core.studio.diagrams.schema import Diagram

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class DiagramStore(ABC):
    """Versioned storage for authored mermaid diagrams."""

    def save(self, workspace: str, diagram: Diagram) -> Diagram:
        """Validate and store *diagram* as the next version. Raises
        ``ValueError`` on lint problems — an invalid diagram is never
        persisted."""
        problems = diagram.lint()
        if problems:
            raise ValueError("; ".join(problems))
        data = self._read(workspace)
        versions = data.get(diagram.id) or {}
        next_version = max((int(v) for v in versions), default=0) + 1
        stored = Diagram.from_dict(diagram.to_dict())
        stored.version = next_version
        versions[str(next_version)] = stored.to_dict()
        data[diagram.id] = versions
        self._write(workspace, data)
        return stored

    def get(self, workspace: str, diagram_id: str,
            version: Optional[int] = None) -> Optional[Diagram]:
        """One diagram — the exact *version*, or the latest."""
        versions = self._read(workspace).get(diagram_id) or {}
        if not versions:
            return None
        key = str(version) if version is not None else str(max(int(v) for v in versions))
        d = versions.get(key)
        return Diagram.from_dict(d) if d is not None else None

    def list(self, workspace: str) -> List[Diagram]:
        """The latest version of every diagram, sorted by id."""
        out: List[Diagram] = []
        for diagram_id in sorted(self._read(workspace)):
            d = self.get(workspace, diagram_id)
            if d is not None:
                out.append(d)
        return out

    def depicting(self, workspace: str, target: str) -> List[Diagram]:
        """Latest-version diagrams that depict *target* (a change request,
        module, or task id) — the read side of the ontology's ``depicts`` link."""
        return [d for d in self.list(workspace) if target in d.depicts]

    def delete(self, workspace: str, diagram_id: str) -> bool:
        data = self._read(workspace)
        if diagram_id not in data:
            return False
        del data[diagram_id]
        self._write(workspace, data)
        return True

    @abstractmethod
    def _read(self, ws: str) -> Dict[str, Dict[str, Any]]: ...
    @abstractmethod
    def _write(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None: ...


class InMemoryDiagramStore(DiagramStore):
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _read(self, ws: str) -> Dict[str, Dict[str, Any]]:
        return {did: dict(vers) for did, vers in self._data.get(ws, {}).items()}

    def _write(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        self._data[ws] = {did: dict(vers) for did, vers in data.items()}


class JsonDiagramStore(DiagramStore):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"diagrams_{name}.json")

    def _read(self, ws: str) -> Dict[str, Dict[str, Any]]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"DiagramStore could not read {p}: {e}")
            return {}

    def _write(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
