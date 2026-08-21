"""PostgreSQL runs quadruple mode, and the D-039 refusal does not come back (P9, D-053).

**This file replaces `test_quadruple_backend_refusal.py`**, which asserted the
opposite and was correct while it was true. A4 v5 said the `decisions` and
`communities` vector stores had no tables in `PGVectorStorage`, so the server
refused the pair at startup rather than dying forty frames deep on the first
governed write. A4 **v8** says they are ordinary tables. The refusal is deleted
rather than disabled, which is what the work plan asked for — and deleted code
comes back, so the first half of this file is a test that notices.

## What earned the amendment

A round-trip on **live PostgreSQL**, not a fixture: `weave` on 5442,
`vector 0.8.5`. Both stores, upsert → query by vector → delete. It is
reproduced below as `test_the_two_stores_round_trip_on_live_postgres`, so the
evidence for the contract sentence is re-runnable rather than quoted from a
transcript.

**Three rows per store, not one.** A single row comes back for any probe, so
*"query returned 1"* shows the SQL executes and says nothing about whether the
store ranks — the same way `test_all_three_graph_adapters_import` read as
coverage of a path that had never run (W30). Each probe here must rank the
right row **first** against two plausible distractors.

## The fourth place

Adding a vector namespace to this adapter means `NAMESPACE_TABLE_MAP`, a DDL,
`PGVectorStorage.upsert`'s dispatch, and `PGVectorStorage.query`'s
`SQL_TEMPLATES` lookup. Missing any of the last three fails loudly on first use.

The fourth was **`PostgreSQLDB.check_tables`**, in a different class, reached at
*connect* time — before any test that constructs a vector store gets to run. It
held a hardcoded set of the three vector tables it must skip, because only
`setup_table()` knows the embedding dimension. A fourth vector table was not on
it, so startup executed the template verbatim and PostgreSQL answered
`invalid input syntax for type integer: "dimension"`.

It is now **derived** — skip any table whose DDL carries the
`VECTOR(dimension)` placeholder — and that is asserted below, because deriving
it is the part that makes the next vector store work without anyone editing a
list they have no reason to know exists.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import uuid
import zlib

import pytest

from weave_core.graph.storage.postgres import (
    NAMESPACE_TABLE_MAP,
    SQL_TEMPLATES,
    TABLES,
    PGVectorStorage,
    namespace_to_table_name,
)
from weave_core.namespace import NameSpace

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: The two namespaces quadruple mode creates unconditionally, with the table
#: each must resolve to.
GOVERNANCE_STORES = {
    NameSpace.VECTOR_STORE_DECISIONS: "WEAVE_VDB_DECISIONS",
    NameSpace.VECTOR_STORE_COMMUNITIES: "WEAVE_VDB_COMMUNITIES",
}


# ── 1 · the refusal is gone, and stays gone ──────────────────────────────────


@pytest.mark.offline
def test_the_startup_refusal_is_deleted_not_disabled():
    """D-039's refusal must not exist in any form.

    Checked by import rather than by reading the file, so a re-export from
    somewhere else counts as coming back. The work plan's wording was
    *"deleted, not disabled"* — a refusal that survives as a constant nobody
    consults is the disabled version, and it reads to the next person as though
    the gap is still open.
    """
    import weave_core.graph.storage as storage

    for symbol in ("QUADRUPLE_UNSUPPORTED_VECTOR_STORES", "QuadrupleUnsupported",
                   "assert_quadruple_supported"):
        assert not hasattr(storage, symbol), (
            f"{symbol} is back in weave_core.graph.storage. PostgreSQL runs "
            "quadruple mode (A4 v8, D-053) — if that has stopped being true, "
            "amend the contract first rather than re-adding the refusal."
        )


@pytest.mark.offline
def test_no_startup_precondition_refuses_this_pair_again():
    """The refusal could return anywhere on the startup path, not only where it
    lived. So this asks the **preconditions themselves**: none of them may
    reject quadruple mode on PostgreSQL.

    Driven rather than grepped — a source scan would flag the sentence in this
    docstring, which is how seven earlier guards in this suite failed.
    """
    import argparse

    from weave.server.app import assert_startup_preconditions
    from weave.server.auth import DEFAULT_TOKEN_SECRET  # noqa: F401 - documents the default

    args = argparse.Namespace(
        token_secret="a-perfectly-fine-secret-that-is-long-enough-xxxx",
        vector_storage="PGVectorStorage", use_quadruple=True,
        event_bus="inprocess", workers=1,
    )
    assert_startup_preconditions(args)  # must not raise


# ── 2 · all four places know the two namespaces ──────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("namespace,table", sorted(GOVERNANCE_STORES.items()))
def test_the_namespace_resolves_to_a_table(namespace, table):
    assert NAMESPACE_TABLE_MAP[namespace] == table
    assert namespace_to_table_name(namespace) == table
    assert table in TABLES, f"{table} has no DDL"


@pytest.mark.offline
@pytest.mark.parametrize("namespace", sorted(GOVERNANCE_STORES))
def test_upsert_dispatches_on_the_namespace(namespace):
    """`PGVectorStorage.upsert` ends in `raise ValueError(... is not supported)`,
    so a namespace with a table and no dispatch branch fails at the first
    governed write — after the server has started and reported itself healthy."""
    source = ast.parse(pathlib.Path(
        _REPO / "weave_core" / "graph" / "storage" / "postgres.py").read_text(encoding="utf-8"))
    builder = f"_upsert_{namespace}"
    methods = [
        node.name for cls in ast.walk(source)
        if isinstance(cls, ast.ClassDef) and cls.name == "PGVectorStorage"
        for node in cls.body if isinstance(node, ast.FunctionDef)
    ]
    assert builder in methods, (
        f"{builder} is not a method of PGVectorStorage. It was defined on "
        "PGKVStorage once — the module imported cleanly, ruff was silent, and "
        "the failure was an AttributeError on the first upsert."
    )
    assert hasattr(PGVectorStorage, builder)


@pytest.mark.offline
@pytest.mark.parametrize("namespace", sorted(GOVERNANCE_STORES))
def test_query_has_a_template_keyed_by_the_namespace(namespace):
    """`query` looks up `SQL_TEMPLATES[self.namespace]` — keyed by the namespace
    string, not by the table name, which is easy to get wrong precisely because
    every other template in the file is keyed the other way."""
    assert namespace in SQL_TEMPLATES, (
        f"SQL_TEMPLATES has no '{namespace}' entry, so PGVectorStorage.query "
        "raises KeyError for a store that upserts perfectly well"
    )


@pytest.mark.offline
def test_the_tables_to_skip_at_startup_are_derived_from_the_ddl():
    """**The fourth place, and the one that had no failure mode a unit test
    would reach.**

    `check_tables` runs at connect. It must not create vector tables, because
    only `setup_table()` knows the embedding dimension to substitute into
    `VECTOR(dimension)`. It used to carry a hardcoded set of three; a fourth
    vector table was not on it, so startup ran the placeholder verbatim.

    Asserting the *derivation* rather than the membership: a test that listed
    the five current tables would need editing by the same person who forgot the
    list in the first place.
    """
    import inspect

    from weave_core.graph.storage.postgres import PostgreSQLDB

    source = inspect.getsource(PostgreSQLDB.check_tables)
    assert "VECTOR(dimension)" in source, (
        "check_tables no longer derives its skip set from the DDL placeholder — "
        "if it is a hardcoded list again, the next vector store will fail at "
        "connect with `invalid input syntax for type integer: \"dimension\"`"
    )

    derived = {name for name, spec in TABLES.items() if "VECTOR(dimension)" in spec["ddl"]}
    assert {"WEAVE_VDB_CHUNKS", "WEAVE_VDB_ENTITY", "WEAVE_VDB_RELATION"} <= derived, (
        "the derivation stopped covering the three original vector tables"
    )
    assert set(GOVERNANCE_STORES.values()) <= derived, (
        "the governance vector tables are not derived as skippable, so "
        "check_tables will try to create them without a dimension"
    )


# ── 3 · the round-trip that earned the amendment ─────────────────────────────

POSTGRES_VARS = ("WEAVE_POSTGRES_HOST", "WEAVE_POSTGRES_USER",
                 "WEAVE_POSTGRES_PASSWORD", "WEAVE_POSTGRES_DATABASE")

requires_postgres = pytest.mark.skipif(
    not all(os.environ.get(v) for v in POSTGRES_VARS),
    reason="needs a live PostgreSQL with pgvector — set " + ", ".join(POSTGRES_VARS),
)

#: Three semantically distinct rows per store, and the probe whose best match
#: must come back **first**. The distractors are real Weave decisions.
#:
#: **The expected winner is inserted last, deliberately.** The first draft put
#: it first, and for `decisions` the correct ranking was then *identical to
#: insertion order* — so that case could not tell a working vector search from a
#: store handing back rows in the order it received them. Only `communities`
#: happened to disambiguate, by luck rather than design. Insert the winner last
#: and the two explanations give different answers, which is the whole job of a
#: distractor.
ROUND_TRIP = {
    NameSpace.VECTOR_STORE_DECISIONS: (
        {
            "dec-2": {"content": "the UI ships as static assets served by the server itself",
                      "src_id": "ADR-011", "tgt_id": "weave-ui"},
            "dec-3": {"content": "dev hosts are outbound-only and the hub never dials them",
                      "src_id": "ADR-015", "tgt_id": "dev-host"},
            "dec-1": {"content": "chose PostgreSQL over the file store for multiple workspaces",
                      "src_id": "ADR-007", "tgt_id": "PostgreSQL"},
        },
        "which storage path supports many workspaces?",
        "dec-1",
    ),
    NameSpace.VECTOR_STORE_COMMUNITIES: (
        {
            "com-2": {"content": "the review workflow\nwho signs off on a milestone",
                      "community_id": "c2", "title": "reviews", "size": 7},
            "com-3": {"content": "container networking for the dev fleet",
                      "community_id": "c3", "title": "fleet networking", "size": 3},
            "com-1": {"content": "storage decisions\nhow the three paths differ",
                      "community_id": "c1", "title": "storage decisions", "size": 4},
        },
        "how do the database backends compare?",
        "com-1",
    ),
}


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize("namespace", sorted(ROUND_TRIP))
@pytest.mark.asyncio
async def test_the_two_stores_round_trip_on_live_postgres(namespace):
    """Upsert, retrieve by vector, delete — on a real database.

    **A pass on the file-based path would prove nothing here**, which is the
    whole reason this is marked `integration` and skips loudly rather than
    falling back to something that runs everywhere.

    Deterministic embeddings on purpose: what is under test is the adapter's
    SQL and the pgvector column, not an embedding model. A hashed unit vector
    keeps the ranking assertion meaningful — the probe embeds to something
    nearer the intended row than the distractors — while making the test free,
    offline-capable against a local database, and identical on every run.
    """
    import numpy as np

    from weave_core.graph.storage.postgres import ClientManager
    from weave_core.store.locks import initialize_share_data
    from weave_core.utils import EmbeddingFunc

    initialize_share_data(1)

    # **The dimension is read from the database, not chosen here.**
    #
    # These tables are shared: the table name carries no dimension, only the
    # workspace column separates callers, and `VECTOR(n)` is fixed when the
    # table is first created. So a test that hardcoded its own `n` would pass on
    # an empty database and fail against any real one with
    # `DataError: expected 1536 dimensions, not 64` — a failure that says
    # nothing about the adapter and everything about who ran first.
    db = await ClientManager.get_client()
    try:
        existing = await db.query(
            "SELECT a.atttypmod AS dim FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = $1 AND a.attname = 'content_vector'",
            [GOVERNANCE_STORES[namespace].lower()],
        )
    finally:
        await ClientManager.release_client(db)
    dim = int(existing["dim"]) if existing and existing.get("dim", -1) > 0 else 64

    def _vector(text: str) -> list[float]:
        """A bag-of-words vector over a fixed hash space. Shared words pull two
        texts together, which is the only property the ranking needs.

        `zlib.crc32` rather than `hash()`: Python randomises string hashing per
        process, so `hash()` would put words in different dimensions on every
        run and the ranking assertion would pass or fail by `PYTHONHASHSEED`.
        A test that is flaky across runs of unchanged code costs more than it
        catches.
        """
        vec = np.zeros(dim, dtype=np.float32)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vec[zlib.crc32(word.encode()) % dim] += 1.0
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm else vec).tolist()

    async def embed(texts, **_kwargs):
        return np.array([_vector(t) for t in texts], dtype=np.float32)

    payload, probe, want = ROUND_TRIP[namespace]
    workspace = f"p9{uuid.uuid4().hex[:8]}"

    store = PGVectorStorage(
        namespace=namespace,
        workspace=workspace,
        global_config={"embedding_batch_num": 8,
                       "vector_db_storage_cls_kwargs": {"cosine_better_than_threshold": -1.0}},
        embedding_func=EmbeddingFunc(embedding_dim=dim, func=embed),
    )
    await store.initialize()
    try:
        await store.upsert(payload)
        await store.index_done_callback()

        hits = await store.query(probe, top_k=3) or []
        found = [hit["id"] for hit in hits]
        assert len(found) == 3, f"expected all three rows back, got {found}"
        assert found[0] == want, (
            f"the store returned rows but did not rank them: wanted {want} "
            f"first, got {found}. {want} was inserted *last*, so returning it "
            "first is vector search and returning it last is insertion order."
        )
        assert found != list(payload), (
            f"the store returned rows in exactly insertion order ({found}), "
            "which is what an unranked SELECT would do"
        )

        # The declared meta fields survive the round-trip with their types.
        top = hits[0]
        for field in sorted(store.meta_fields):
            assert field in top, f"{field} is declared in meta_fields but not returned"
        if namespace == NameSpace.VECTOR_STORE_COMMUNITIES:
            assert top["size"] == 4 and isinstance(top["size"], int), (
                f"size came back as {top['size']!r} — it is an integer column so "
                "an operator can order by it without casting"
            )

        await store.delete([want])
        remaining = [hit["id"] for hit in (await store.query(probe, top_k=3) or [])]
        assert want not in remaining, "delete did not remove the row"
        assert len(remaining) == 2, (
            f"delete removed more than the row it was given: {remaining}"
        )
    finally:
        await store.drop()
        await store.finalize()
