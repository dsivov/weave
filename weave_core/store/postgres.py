"""The PostgreSQL ``RecordStore`` adapter — the multi-user persistence path (A4).

The third implementation of the port defined in :mod:`weave_core.store.record`,
alongside the in-memory and JSON ones that came across with the fork. Users,
dev hosts, workers and project layouts all persist through it without changing a
line, which is the whole point of having a port (D-020).

One table, one row per record, the record itself in ``jsonb``. That is not
laziness: the records are small, they are always fetched whole by id or listed
per workspace, and every one of them evolves independently. A column per field
would mean a migration every time a record type gains one, which is exactly the
kind of friction that makes people store things somewhere else instead.

**Why this module owns a background event loop.** The port is synchronous —
``save`` / ``get`` / ``list`` / ``delete`` — because that is what its existing
callers are, including the dev-host daemon that runs nowhere near a web server.
The only PostgreSQL driver in the dependency set is ``asyncpg``, which is
async-only, and adding a synchronous driver would be a new library (A11). So the
adapter keeps one event loop on one daemon thread and hands coroutines to it.
The alternatives were worse: making the port async would rewrite every caller
including the copied fleet code, and a second synchronous port would be two
tools for one job (R10).

This is a **tripwire** — background work where there was none — and it is
reported, not smuggled. It is contained: the thread starts on first use, belongs
to this module, and nothing outside it can see a coroutine.

Callers on an event loop should reach this through a threadpool (FastAPI does
that for ``def`` endpoints automatically), or they will block their own loop for
the length of a round trip.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Dict, List, Optional

from weave_core.store.record import RecordStore
from weave_core.utils import logger

#: The single table every record store shares, partitioned logically by
#: ``store`` and ``workspace`` rather than by having a table each.
DEFAULT_TABLE = "weave_records"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    store       TEXT        NOT NULL,
    workspace   TEXT        NOT NULL,
    record_id   TEXT        NOT NULL,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store, workspace, record_id)
);
CREATE INDEX IF NOT EXISTS {table}_scan ON {table} (store, workspace);
"""


class _LoopThread:
    """One event loop, on one daemon thread, shared by every Postgres store.

    Created lazily so that importing this module costs nothing on a deployment
    that never selects the PostgreSQL path.
    """

    _instance: Optional["_LoopThread"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="weave-recordstore-pg", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @classmethod
    def instance(cls) -> "_LoopThread":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def run(self, coro, timeout: float = 30.0):
        """Run *coro* on the loop thread and block until it finishes."""
        if self._loop.is_closed():  # pragma: no cover - only after shutdown
            raise RuntimeError("the record-store event loop has been shut down")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)


def connection_settings(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Read the PostgreSQL connection from the configuration surface (D-024).

    Every one of these is a variable Weave itself reads, so every one carries
    the ``WEAVE_`` prefix. Getting a name wrong here does not misconfigure the
    connection — it refuses the deployment, because the storage registry checks
    for exactly these names before the engine starts.
    """
    env = os.environ if env is None else env
    return {
        "host": env.get("WEAVE_POSTGRES_HOST", "localhost"),
        "port": int(env.get("WEAVE_POSTGRES_PORT", 5432)),
        "user": env.get("WEAVE_POSTGRES_USER", "weave"),
        "password": env.get("WEAVE_POSTGRES_PASSWORD", ""),
        "database": env.get("WEAVE_POSTGRES_DATABASE", "weave"),
    }


class PostgresRecordStore(RecordStore):
    """A ``RecordStore`` backed by one PostgreSQL table.

    Subclass it exactly as the JSON store is subclassed — set ``record_type``
    and a ``store_name`` — so a registry moves between paths by construction
    rather than by having a second implementation.
    """

    #: Logical partition within the shared table. Mirrors ``filename_prefix``
    #: on the JSON store so the two are obviously the same concept.
    store_name: str = "weave_records"

    def __init__(
        self,
        *,
        settings: Optional[Dict[str, Any]] = None,
        table: str = DEFAULT_TABLE,
        min_size: int = 1,
        max_size: int = 8,
    ) -> None:
        self._settings = settings or connection_settings()
        self._table = table
        self._min_size = min_size
        self._max_size = max_size
        self._pool = None
        self._ready = False
        self._lock = threading.Lock()

    # -- plumbing -----------------------------------------------------------

    def _loop(self) -> _LoopThread:
        return _LoopThread.instance()

    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg  # imported here: the driver is only needed on this path

            self._pool = await asyncpg.create_pool(
                min_size=self._min_size, max_size=self._max_size, **self._settings
            )
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA.format(table=self._table))
            logger.info(
                f"record store: connected to PostgreSQL "
                f"{self._settings['host']}:{self._settings['port']}/"
                f"{self._settings['database']} (table {self._table})"
            )
        return self._pool

    def _run(self, coro_factory):
        with self._lock:
            ready = self._ready
        if not ready:
            self._loop().run(self._ensure_pool())
            with self._lock:
                self._ready = True
        return self._loop().run(coro_factory())

    def close(self) -> None:
        """Release the pool. Idempotent; safe to call on a store never used."""
        if self._pool is not None:
            pool, self._pool = self._pool, None
            self._ready = False
            self._loop().run(pool.close())

    # -- the port -----------------------------------------------------------

    def _write(self, ws: str, rid: str, d: Dict[str, Any]) -> None:
        async def go():
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""
                    INSERT INTO {self._table} (store, workspace, record_id, data, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, now())
                    ON CONFLICT (store, workspace, record_id)
                    DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                    """,
                    self.store_name, ws, rid, json.dumps(d),
                )

        self._run(lambda: go())

    def _read(self, ws: str, rid: str) -> Optional[Dict[str, Any]]:
        async def go():
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT data FROM {self._table} "
                    f"WHERE store = $1 AND workspace = $2 AND record_id = $3",
                    self.store_name, ws, rid,
                )
                return json.loads(row["data"]) if row else None

        return self._run(lambda: go())

    def _all(self, ws: str) -> List[Dict[str, Any]]:
        async def go():
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"SELECT data FROM {self._table} WHERE store = $1 AND workspace = $2 "
                    f"ORDER BY record_id",
                    self.store_name, ws,
                )
                return [json.loads(r["data"]) for r in rows]

        return self._run(lambda: go())

    def _delete(self, ws: str, rid: str) -> bool:
        async def go():
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                result = await conn.execute(
                    f"DELETE FROM {self._table} "
                    f"WHERE store = $1 AND workspace = $2 AND record_id = $3",
                    self.store_name, ws, rid,
                )
                # asyncpg returns the command tag, e.g. "DELETE 1"
                return result.rsplit(" ", 1)[-1] not in ("0", "")

        return self._run(lambda: go())
