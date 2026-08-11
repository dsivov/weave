"""The resolver sits **inside** the tenant boundary, not beside it (R22a, D-028).

`/projects/resolve` returns file content. If the `ProjectLayout` registry were
global, any authenticated user could read any workspace's repository by naming
it in a locator — and membership (R14, A14) would scope what a user sees *in the
graph* while the resolver handed out the underlying files. That inverts the
guarantee the whole tenancy model exists to give.

So the assertion is specific, and the 404 is the point: a repository registered
in another workspace must be **indistinguishable** from one that does not exist
anywhere. Not a 403 (which confirms it exists), not a different message, not a
different shape. Anything that distinguishes the two is an enumeration oracle
for other tenants' repository names.

**This runs against real PostgreSQL as well as in memory**, because the property
under test is that the workspace argument reaches the layer that decides which
rows come back. An in-memory dict keyed on that argument cannot fail the test
even if the real adapter ignored it — which is exactly how the workspace-header
defect survived two milestone reviews (D-030). The PostgreSQL run is an M2 gate
item, and it skips with the assumption named rather than passing quietly.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave.model.locator import Locator
from weave.model.project_layout import (
    InMemoryProjectLayoutStore,
    JsonProjectLayoutStore,
    NotRegistered,
    ProjectLayout,
    ProjectLayoutRegistry,
    ProjectLayoutStore,
)
from weave.server.routers.projects import create_project_routes
from weave.server.workspace_pool import WORKSPACE_HEADER, get_workspace_middleware
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
        "PostgreSQL is not configured (AS2 unverified in this run, and the "
        "tenant boundary is only proven in memory) — set "
        + ", ".join(POSTGRES_VARS)
    ),
)


class _PgProjectLayoutStore(PostgresRecordStore):
    record_type = ProjectLayout
    store_name = "weave_projects_test"


# ── store fixtures: the same assertions run against each backend ─────────────


@pytest.fixture
def memory_store() -> ProjectLayoutStore:
    return InMemoryProjectLayoutStore()


@pytest.fixture
def json_store(tmp_path) -> ProjectLayoutStore:
    return JsonProjectLayoutStore(str(tmp_path))


@pytest.fixture
def postgres_store():
    store = _PgProjectLayoutStore(settings=connection_settings())
    yield store
    store.close()


@pytest.fixture
def workspaces():
    """Unique names, so a PostgreSQL run does not collide with a previous one."""
    tag = uuid.uuid4().hex[:8]
    return f"alpha_{tag}", f"beta_{tag}"


def _client(registry: ProjectLayoutRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(create_project_routes(registry))

    class _StubPool:
        async def get_rag(self, workspace):
            return object()

    app.add_middleware(get_workspace_middleware(_StubPool(), "default"))
    return TestClient(app)


def _assert_boundary_holds(store: ProjectLayoutStore, alpha: str, beta: str) -> None:
    """One repository, registered in `alpha` only. Run against every backend.

    Deliberately one body for every store: a boundary that holds in memory and
    leaks on PostgreSQL is a boundary whose port leaks, and the only way to see
    that is to make the paths answer identical questions.
    """
    registry = ProjectLayoutRegistry(store)
    registry.register(
        alpha, "secret-repo",
        clone_url="https://github.com/example/secret-repo.git",
        description="registered in alpha only",
    )

    # The owning workspace sees it.
    assert [p.name for p in registry.list(alpha)] == ["secret-repo"]
    assert registry.require(alpha, "secret-repo").description == "registered in alpha only"

    # The other workspace does not — not in the listing…
    assert registry.list(beta) == [], "a repository leaked into another workspace"
    assert registry.get(beta, "secret-repo") is None

    # …and not through resolve, which is the call that would return content.
    with pytest.raises(NotRegistered):
        registry.resolve(beta, Locator("secret-repo", "README.md", "abc123"))


@pytest.mark.offline
def test_the_boundary_holds_in_memory(memory_store, workspaces):
    _assert_boundary_holds(memory_store, *workspaces)


@pytest.mark.offline
def test_the_boundary_holds_on_the_file_path(json_store, workspaces, tmp_path):
    alpha, beta = workspaces
    _assert_boundary_holds(json_store, alpha, beta)

    # The file path keeps one file per workspace, so the separation is visible
    # on disk rather than only in the answers.
    written = sorted(p.name for p in tmp_path.glob("weave_projects_*.json"))
    assert written == [f"weave_projects_{alpha}.json"]


@pytest.mark.integration
@requires_postgres
def test_the_boundary_holds_on_postgresql(postgres_store, workspaces):
    """The gate item. The workspace must reach the WHERE clause, not just the
    dictionary key."""
    alpha, beta = workspaces
    try:
        _assert_boundary_holds(postgres_store, alpha, beta)
    finally:
        postgres_store.delete(alpha, "secret-repo")


# ── the same thing over HTTP, which is where a caller actually stands ────────


@pytest.mark.offline
def test_an_unregistered_repository_is_a_bare_404_over_http(memory_store, workspaces):
    alpha, beta = workspaces
    registry = ProjectLayoutRegistry(memory_store)
    registry.register(alpha, "secret-repo", clone_url="https://github.com/example/x")

    with _client(registry) as client:
        query = {"repo": "secret-repo", "path": "README.md", "rev": "abc123"}
        mine = client.get("/projects/resolve", params=query,
                          headers={WORKSPACE_HEADER: alpha})
        theirs = client.get("/projects/resolve", params=query,
                            headers={WORKSPACE_HEADER: beta})
        nonexistent = client.get(
            "/projects/resolve",
            params={"repo": "no-such-repo", "path": "README.md", "rev": "abc123"},
            headers={WORKSPACE_HEADER: beta},
        )

    assert mine.status_code == 200
    assert theirs.status_code == 404, "another workspace's repository resolved"

    # The assertion that matters: "someone else's" and "nobody's" are the same
    # response, byte for byte. A 403, or a differing message, would confirm the
    # repository exists and turn the endpoint into an enumeration oracle.
    assert theirs.status_code == nonexistent.status_code
    assert theirs.json() == nonexistent.json()


@pytest.mark.offline
def test_listing_projects_shows_only_the_callers_workspace(memory_store, workspaces):
    alpha, beta = workspaces
    registry = ProjectLayoutRegistry(memory_store)
    registry.register(alpha, "alpha-repo", clone_url="https://github.com/example/a")
    registry.register(beta, "beta-repo", clone_url="https://github.com/example/b")

    with _client(registry) as client:
        seen_by_alpha = client.get("/projects", headers={WORKSPACE_HEADER: alpha}).json()
        seen_by_beta = client.get("/projects", headers={WORKSPACE_HEADER: beta}).json()

    assert [p["name"] for p in seen_by_alpha["projects"]] == ["alpha-repo"]
    assert [p["name"] for p in seen_by_beta["projects"]] == ["beta-repo"]
    assert seen_by_alpha["workspace"] == alpha


@pytest.mark.offline
def test_registration_lands_in_the_callers_workspace_not_the_default(
    memory_store, workspaces
):
    """Registration is workspace-scoped through the same header. This would have
    failed while the middleware was ignoring it (D-030), which is why it is
    asserted here and not assumed from the resolve tests."""
    alpha, _ = workspaces
    registry = ProjectLayoutRegistry(memory_store)

    with _client(registry) as client:
        created = client.post(
            "/projects",
            json={"name": "alpha-repo", "clone_url": "https://github.com/example/a"},
            headers={WORKSPACE_HEADER: alpha},
        )

    assert created.status_code == 200
    assert [p.name for p in registry.list(alpha)] == ["alpha-repo"]
    assert registry.list("default") == [], "registration landed in the default workspace"


@pytest.mark.offline
def test_a_shared_repository_is_registered_in_each_workspace(memory_store, workspaces):
    """R22b: duplicating a four-field record is cheaper than a hole in the tenant
    boundary, and it keeps the store's workspace-first signature honest."""
    alpha, beta = workspaces
    registry = ProjectLayoutRegistry(memory_store)
    for workspace in (alpha, beta):
        registry.register(workspace, "shared", clone_url="https://github.com/example/s")

    assert registry.require(alpha, "shared").name == "shared"
    assert registry.require(beta, "shared").name == "shared"

    # And they are genuinely separate records: unregistering one leaves the other.
    registry.unregister(alpha, "shared")
    assert registry.get(alpha, "shared") is None
    assert registry.get(beta, "shared") is not None


@pytest.mark.offline
def test_the_server_side_checkout_path_is_not_published(memory_store, workspaces):
    """`local_path` is a server filesystem path. It is what the resolver reads
    and it is nobody's business over HTTP."""
    alpha, _ = workspaces
    registry = ProjectLayoutRegistry(memory_store)
    registry.register(
        alpha, "weave",
        clone_url="https://github.com/example/weave",
        local_path="/srv/checkouts/weave",
    )

    with _client(registry) as client:
        body = client.get("/projects", headers={WORKSPACE_HEADER: alpha}).text

    assert "/srv/checkouts/weave" not in body
    assert "local_path" not in body
