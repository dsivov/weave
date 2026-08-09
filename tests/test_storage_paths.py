"""The user store works on all three storage paths (R8, R9, A4 · AS2, AS3).

Three paths are supported, so three paths are tested — that is the whole reason
the count is three rather than eight (D-007). The DRP flags AS2 (PostgreSQL) and
AS3 (Neo4j) as **unverified**: the running instance was file-based and neither
production path had ever been exercised. This is where that stops being an
assumption.

The PostgreSQL tests are skipped, loudly and by name, when no database is
configured. A skip that says *why* is honest; a suite that quietly tests one path
and reports three is not, and is exactly how AS2 stayed unverified for as long as
it did. Point them at a database with:

    WEAVE_POSTGRES_HOST=localhost WEAVE_POSTGRES_PORT=5442 \\
    WEAVE_POSTGRES_USER=weave WEAVE_POSTGRES_PASSWORD=... \\
    WEAVE_POSTGRES_DATABASE=weave pytest tests/test_storage_paths.py

Neo4j is a **graph** backend, not a record store: there is no user store on it,
and A4 does not ask for one. What is asserted here is that its adapter resolves
and that its configuration surface is prefixed, with the graph-level exercise
belonging to the engine's own suite.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from weave.server.users import (
    InMemoryUserStore,
    JsonUserStore,
    User,
    UserService,
    verify_password,
)
from weave_core.store.postgres import PostgresRecordStore, connection_settings

POSTGRES_VARS = (
    "WEAVE_POSTGRES_HOST",
    "WEAVE_POSTGRES_USER",
    "WEAVE_POSTGRES_PASSWORD",
    "WEAVE_POSTGRES_DATABASE",
)

postgres_configured = all(os.environ.get(v) for v in POSTGRES_VARS)
requires_postgres = pytest.mark.skipif(
    not postgres_configured,
    reason=(
        "PostgreSQL is not configured (AS2 unverified in this run) — set "
        + ", ".join(POSTGRES_VARS)
    ),
)


class _PgUserStore(PostgresRecordStore):
    record_type = User
    store_name = "weave_users_test"


def _exercise(service: UserService) -> None:
    """One contract, run against whichever backend was handed in.

    Deliberately the same body for every path: a store that passes on JSON and
    fails on PostgreSQL is a store whose port leaks, and the only way to see
    that is to make the paths answer identical questions.
    """
    created = service.create(
        "alice", "a-good-password", role="architect",
        display_name="Alice", email="alice@example.com",
        workspaces=["alpha", "beta"], granted_by="root",
    )

    # read back by id
    fetched = service.require(created.id)
    assert fetched.username == "alice"
    assert fetched.role == "architect"
    assert fetched.workspaces == ["alpha", "beta"]
    assert verify_password("a-good-password", fetched.password_hash)

    # read back by username, and authenticate
    assert service.by_username("alice").id == created.id
    assert service.authenticate("alice", "a-good-password") is not None
    assert service.authenticate("alice", "wrong-password") is None

    # list
    service.create("bob", "a-good-password", workspaces=["beta"])
    assert [u.username for u in service.list_users()] == ["alice", "bob"]
    assert [u.username for u in service.members_of("alpha")] == ["alice"]
    assert [u.username for u in service.members_of("beta")] == ["alice", "bob"]

    # update, and the grant provenance that survives it
    service.set_workspaces(created.id, ["alpha", "gamma"], granted_by="someone-else")
    grants = {m.workspace: m for m in service.require(created.id).memberships}
    assert set(grants) == {"alpha", "gamma"}
    assert grants["alpha"].granted_by == "root", "provenance lost on this backend"

    # password change
    service.set_password(created.id, "a-different-password")
    assert service.authenticate("alice", "a-different-password") is not None

    # delete
    assert service.delete(created.id) is True
    assert service.get(created.id) is None
    assert service.delete(created.id) is False
    assert [u.username for u in service.list_users()] == ["bob"]


# ── path 1 · in-memory (what the tests run on) ───────────────────────────────


@pytest.mark.offline
def test_the_in_memory_path():
    _exercise(UserService(InMemoryUserStore()))


# ── path 2 · file-based (the default, single-operator only) ──────────────────


@pytest.mark.offline
def test_the_file_based_path():
    with tempfile.TemporaryDirectory() as d:
        _exercise(UserService(JsonUserStore(d)))


@pytest.mark.offline
def test_the_file_based_path_survives_a_restart():
    """A record store that loses data on restart is not a store."""
    with tempfile.TemporaryDirectory() as d:
        first = UserService(JsonUserStore(d))
        created = first.create("alice", "a-good-password", workspaces=["alpha"])

        # a brand-new process would build a brand-new store over the same directory
        second = UserService(JsonUserStore(d))
        reread = second.require(created.id)
        assert reread.username == "alice"
        assert reread.workspaces == ["alpha"]
        assert second.authenticate("alice", "a-good-password") is not None


# ── path 3 · PostgreSQL (AS2 — never previously exercised) ───────────────────


@requires_postgres
@pytest.mark.integration
def test_the_postgres_path():
    store = _PgUserStore()
    store.store_name = f"users_test_{uuid.uuid4().hex[:8]}"   # isolate this run
    try:
        _exercise(UserService(store))
    finally:
        store.close()


@requires_postgres
@pytest.mark.integration
def test_the_postgres_path_survives_a_restart():
    name = f"users_test_{uuid.uuid4().hex[:8]}"

    first = _PgUserStore()
    first.store_name = name
    created = UserService(first).create("alice", "a-good-password", workspaces=["alpha"])
    first.close()

    second = _PgUserStore()
    second.store_name = name
    try:
        service = UserService(second)
        reread = service.require(created.id)
        assert reread.username == "alice"
        assert reread.workspaces == ["alpha"]
        assert service.authenticate("alice", "a-good-password") is not None
    finally:
        second.close()


@requires_postgres
@pytest.mark.integration
def test_two_stores_do_not_see_each_others_records():
    """The shared table is partitioned by store name; prove the partition holds.

    One table for every registry is only safe if the partition is real — a leak
    here would show up as dev hosts appearing in the user list.
    """
    users = _PgUserStore()
    users.store_name = f"users_test_{uuid.uuid4().hex[:8]}"
    others = _PgUserStore()
    others.store_name = f"hosts_test_{uuid.uuid4().hex[:8]}"
    try:
        UserService(users).create("alice", "a-good-password")
        assert UserService(others).list_users() == []
        assert len(UserService(users).list_users()) == 1
    finally:
        users.close()
        others.close()


@requires_postgres
@pytest.mark.integration
def test_workspaces_are_isolated_from_each_other():
    store = _PgUserStore()
    store.store_name = f"users_test_{uuid.uuid4().hex[:8]}"
    try:
        store.save("alpha", User(id="1", username="alice", password_hash="x"))
        store.save("beta", User(id="2", username="bob", password_hash="x"))
        assert [u.username for u in store.list("alpha")] == ["alice"]
        assert [u.username for u in store.list("beta")] == ["bob"]
        assert store.get("alpha", "2") is None
    finally:
        store.close()


# ── the configuration surface for the two production paths ──────────────────


@pytest.mark.offline
def test_postgres_settings_come_from_prefixed_variables():
    settings = connection_settings(env={
        "WEAVE_POSTGRES_HOST": "db.internal",
        "WEAVE_POSTGRES_PORT": "5442",
        "WEAVE_POSTGRES_USER": "weave",
        "WEAVE_POSTGRES_PASSWORD": "secret",
        "WEAVE_POSTGRES_DATABASE": "weave",
    })
    assert settings == {
        "host": "db.internal", "port": 5442, "user": "weave",
        "password": "secret", "database": "weave",
    }


@pytest.mark.offline
def test_all_three_graph_adapters_import():
    """A4's three paths, resolved the way the engine resolves them at startup."""
    import importlib

    from weave_core.graph.storage import STORAGES

    for name, module_path in STORAGES.items():
        module = importlib.import_module(module_path, package="weave_core")
        assert hasattr(module, name), f"{module_path} does not define {name}"


@pytest.mark.offline
def test_the_neo4j_adapter_is_configured_by_prefixed_variables():
    """AS3's configuration half. Neo4j is a graph backend, not a record store —
    there is no user store on it and A4 does not ask for one."""
    from weave_core.graph.storage import STORAGE_ENV_REQUIREMENTS

    assert STORAGE_ENV_REQUIREMENTS["Neo4JStorage"] == [
        "WEAVE_NEO4J_URI", "WEAVE_NEO4J_USERNAME", "WEAVE_NEO4J_PASSWORD",
    ]


# ── path 3b · Neo4j, the optional dedicated graph engine (AS3) ───────────────

NEO4J_VARS = ("WEAVE_NEO4J_URI", "WEAVE_NEO4J_USERNAME", "WEAVE_NEO4J_PASSWORD")

neo4j_configured = all(os.environ.get(v) for v in NEO4J_VARS)
requires_neo4j = pytest.mark.skipif(
    not neo4j_configured,
    reason=(
        "Neo4j is not configured (AS3 unverified in this run) — set "
        + ", ".join(NEO4J_VARS)
    ),
)


@requires_neo4j
@pytest.mark.integration
async def test_the_neo4j_graph_path():
    """AS3, exercised rather than assumed.

    The DRP records this path as "unverified — may not work at all", because the
    running instance was file-based and nobody had ever pointed the fork at a
    Neo4j. Nodes, edges, degree, traversal and delete, against a real server.
    """
    from weave_core.graph.storage.neo4j import Neo4JStorage
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)
    store = Neo4JStorage(
        namespace=f"t{uuid.uuid4().hex[:8]}",
        workspace="alpha",
        global_config={"embedding_batch_num": 8},
        embedding_func=None,
    )
    await store.initialize()
    try:
        await store.upsert_node("alice", {
            "entity_id": "alice", "entity_type": "person", "description": "a person"})
        await store.upsert_node("bob", {
            "entity_id": "bob", "entity_type": "person", "description": "another"})
        await store.upsert_edge("alice", "bob", {
            "weight": 1.0, "description": "knows", "keywords": "social"})

        assert await store.has_node("alice")
        assert await store.has_edge("alice", "bob")
        assert (await store.get_node("alice"))["description"] == "a person"
        assert (await store.get_edge("alice", "bob"))["description"] == "knows"
        assert await store.node_degree("alice") == 1

        graph = await store.get_knowledge_graph("*", max_depth=2, max_nodes=10)
        assert len(graph.nodes) == 2 and len(graph.edges) == 1

        await store.delete_node("bob")
        assert not await store.has_node("bob")
    finally:
        await store.drop()
        await store.finalize()


@pytest.mark.offline
def test_neo4j_community_cannot_isolate_workspaces_by_database():
    """A limitation worth writing down rather than rediscovering (R11).

    The adapter asks Neo4j for a database per workspace and falls back to the
    default one when the server refuses — which Community Edition always does,
    since multi-database is an Enterprise feature. The fallback keeps a single
    workspace working perfectly and is exactly the wrong shape for several:
    every workspace would share one database, and A4's isolation would hold only
    because nobody had created a second workspace yet.

    Recorded here so the M1 review can decide between "Neo4j requires Enterprise
    for multi-workspace" and "Neo4j ships labelled experimental" (R11) with the
    fact in hand rather than as a surprise in production.
    """
    import inspect

    from weave_core.graph.storage import neo4j as adapter

    source = inspect.getsource(adapter)
    assert "Fallback to use the default database" in source, (
        "the per-workspace-database fallback changed; re-check what isolation "
        "Neo4j actually provides before relying on it"
    )
