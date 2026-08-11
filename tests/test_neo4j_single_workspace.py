"""A second workspace on the Neo4j path is refused, in code (A4 v4, D-029).

Neo4j Community has no multi-database support, so every workspace on that path
shares one database and is separated only by a label. The M1 review proposed
qualifying A4 to say so. dsivov chose the narrower promise instead, and the
reasoning is the point: a qualification annotates the failure but leaves it
available — an operator who reads the caveat and proceeds anyway gets no error,
just silent co-tenancy, which is the same shape as the in-process bus under
multiple workers (D-019). So the restriction became code.

**These tests assert the class, not the call site.** The property is
*backend-dependent workspace admission*: the same call succeeds on the paths that
can hold many workspaces and is refused on the one that cannot. Testing only
that one `if` fires would pass just as well if the rule were bolted to the Neo4j
adapter, or checked at read time, or applied to every backend.

Two further properties matter and are asserted separately:

- the refusal is **actionable** — it names the workspace already holding the
  backend and what to move to, because an operator who hits it must act rather
  than retry;
- the refusal **survives a restart**. A guard resting on this process's
  dictionary would let a fresh server admit a different workspace into an
  occupied database — the documented-only restriction again, in a new hat.
"""

from __future__ import annotations

import os

import pytest

from weave.server.workspace_admission import (
    SINGLE_WORKSPACE_GRAPH_BACKENDS,
    WorkspaceNotAdmitted,
    check_admission,
)
from weave.server.workspace_pool import WorkspacePool

NEO4J = "Neo4JStorage"

#: The graph backends the contract says may hold many workspaces.
MULTI_WORKSPACE_BACKENDS = ["NetworkXStorage", "PGGraphStorage"]

NEO4J_VARS = ("WEAVE_NEO4J_URI", "WEAVE_NEO4J_USERNAME", "WEAVE_NEO4J_PASSWORD")
neo4j_configured = all(os.environ.get(v) for v in NEO4J_VARS)
requires_neo4j = pytest.mark.skipif(
    not neo4j_configured,
    reason=(
        "Neo4j is not configured (AS3 unverified in this run, so the D-029 "
        "refusal is exercised only against a stub) — set " + ", ".join(NEO4J_VARS)
    ),
)


async def _empty_probe() -> set:
    return set()


def _probe(*workspaces):
    async def probe() -> set:
        return set(workspaces)
    return probe


class _FakeRag:
    """Enough of a rag for the pool: it only initialises and finalises."""

    def __init__(self, workspace, **kwargs):
        self.workspace = workspace

    async def initialize_storages(self):
        return None

    async def check_and_migrate_data(self):
        return None

    async def finalize_storages(self):
        return None


def _pool(graph_storage: str, probe=None) -> WorkspacePool:
    return WorkspacePool(
        rag_cls=_FakeRag,
        rag_kwargs={"graph_storage": graph_storage},
        admission_probe=probe or _empty_probe,
    )


# ── the class: admission depends on the backend ──────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("graph_storage", MULTI_WORKSPACE_BACKENDS)
async def test_a_second_workspace_is_admitted_on_a_multi_workspace_backend(
    graph_storage,
):
    """The other half of the assertion. Without this, a rule that refused *every*
    second workspace would pass the Neo4j test and break the product."""
    pool = _pool(graph_storage)
    await pool.get_rag("alpha")
    await pool.get_rag("beta")

    assert sorted(pool.workspaces) == ["alpha", "beta"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_second_workspace_is_refused_on_the_neo4j_path():
    pool = _pool(NEO4J)
    await pool.get_rag("alpha")

    with pytest.raises(WorkspaceNotAdmitted) as exc:
        await pool.get_rag("beta")

    assert pool.workspaces == ["alpha"], "the refused workspace was created anyway"
    assert "beta" in str(exc.value) and "alpha" in str(exc.value)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_first_workspace_always_succeeds_on_the_neo4j_path():
    """Experimental and single-workspace, not unusable."""
    pool = _pool(NEO4J)
    rag = await pool.get_rag("alpha")
    assert rag.workspace == "alpha"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_reopening_the_same_workspace_is_not_creating_a_second_one():
    pool = _pool(NEO4J)
    first = await pool.get_rag("alpha")
    again = await pool.get_rag("alpha")
    assert first is again


# ── the refusal is actionable ────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_refusal_names_the_limit_the_holder_and_the_way_out():
    """An operator reading this message should not have to consult the contract
    to know what to do next."""
    with pytest.raises(WorkspaceNotAdmitted) as exc:
        await check_admission("beta", NEO4J, probe=_probe("alpha"))

    message = str(exc.value)
    assert "beta" in message                      # what was refused
    assert "alpha" in message                     # what holds the backend
    assert "Community" in message                 # why — the edition limit
    assert "PostgreSQL" in message                # where to go instead
    assert "WEAVE_GRAPH_STORAGE" in message       # how, concretely


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_refusal_reaches_a_caller_as_409_not_500():
    """A refusal by policy is a conflict, not a server fault. A 500 would read
    as "try again", which is the one thing that cannot work."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from weave.server.workspace_pool import (
        WORKSPACE_HEADER,
        get_workspace_middleware,
    )

    app = FastAPI()

    @app.get("/probe")
    async def probe():
        return {}

    pool = _pool(NEO4J)
    await pool.get_rag("alpha")
    app.add_middleware(get_workspace_middleware(pool, "alpha"))

    with TestClient(app) as client:
        ok = client.get("/probe", headers={WORKSPACE_HEADER: "alpha"})
        refused = client.get("/probe", headers={WORKSPACE_HEADER: "beta"})

    assert ok.status_code == 200
    assert refused.status_code == 409
    assert "Community" in refused.json()["detail"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_seeded_default_workspace_is_admitted_too():
    """The door that skips the creation check.

    `seed()` registers the deployment's default workspace synchronously, before
    anything can ask the database a question. A deployment whose default is
    `beta`, pointed at a Neo4j already holding `alpha`, would otherwise open a
    second workspace on a single-workspace backend at boot — arriving past the
    guard rather than through it. `finalize_seed()` is where that is caught, and
    the candidate must not count itself as prior occupancy.
    """
    pool = _pool(NEO4J, probe=_probe("alpha"))
    pool.seed("beta")

    with pytest.raises(WorkspaceNotAdmitted):
        await pool.finalize_seed("beta")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_the_seeded_default_is_admitted_when_it_is_the_one_the_database_holds():
    pool = _pool(NEO4J, probe=_probe("alpha"))
    pool.seed("alpha")
    await pool.finalize_seed("alpha")

    assert pool.workspaces == ["alpha"]


# ── the refusal survives a restart ───────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_a_fresh_process_still_refuses_a_workspace_the_database_holds():
    """The property that makes this more than in-process bookkeeping.

    A brand-new pool knows nothing — but the database does, and the policy asks
    it. Without this, restarting the server would admit a second workspace into
    an already-occupied Neo4j database, which is precisely the silent co-tenancy
    D-029 exists to prevent.
    """
    fresh = _pool(NEO4J, probe=_probe("alpha"))
    assert fresh.workspaces == []

    with pytest.raises(WorkspaceNotAdmitted):
        await fresh.get_rag("beta")

    # …and the workspace the database already holds is still reachable.
    assert (await fresh.get_rag("alpha")).workspace == "alpha"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_an_unreachable_database_does_not_take_the_server_down():
    """The probe returning nothing means "could not verify", and an admission
    check must not be the thing that stops a server serving. Refusing to start
    because a limit could not be confirmed is worse than the limit."""
    pool = _pool(NEO4J, probe=_empty_probe)
    assert (await pool.get_rag("alpha")).workspace == "alpha"


# ── the policy is declared, not scattered ────────────────────────────────────


@pytest.mark.offline
def test_the_single_workspace_backends_are_declared_with_a_reason():
    """Adding a backend to this set should be a deliberate act with a stated
    reason, not a string comparison buried in a branch."""
    assert NEO4J in SINGLE_WORKSPACE_GRAPH_BACKENDS
    assert set(SINGLE_WORKSPACE_GRAPH_BACKENDS) == {NEO4J}, (
        "a backend gained a single-workspace limit; if that is right, this test "
        "and A4 both need updating"
    )
    assert "Community" in SINGLE_WORKSPACE_GRAPH_BACKENDS[NEO4J]


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("graph_storage", MULTI_WORKSPACE_BACKENDS + [""])
async def test_backends_without_the_limit_are_never_probed(graph_storage):
    """The check must cost nothing on the paths it does not apply to — and must
    not open a connection to a database this deployment does not use."""
    called = False

    async def probe():
        nonlocal called
        called = True
        return {"alpha"}

    await check_admission("beta", graph_storage, probe=probe)
    assert not called, f"{graph_storage or 'no backend'} was probed for occupancy"


# ── against a real Neo4j (AS3) ───────────────────────────────────────────────


@pytest.mark.integration
@requires_neo4j
@pytest.mark.asyncio
async def test_the_occupancy_probe_reads_a_real_neo4j():
    """D-029 was made code precisely so it could be exercised against a real
    server. This asserts the adapter's probe answers without raising and returns
    labels, not that any particular workspace exists — the database is shared
    with other tests and its contents are not this test's business.
    """
    from weave_core.graph.storage.neo4j import occupied_workspaces

    labels = await occupied_workspaces()
    assert isinstance(labels, set)
    assert all(isinstance(label, str) for label in labels)
    assert "base" not in labels, "the reserved label leaked into occupancy"


@pytest.mark.integration
@requires_neo4j
@pytest.mark.asyncio
async def test_admission_against_a_real_neo4j_is_consistent_with_its_occupancy():
    """The end-to-end shape: whatever the real database holds, admitting a
    workspace it already holds succeeds and admitting a different one is refused.
    Written against the live occupancy rather than a fixed name, so it does not
    depend on what a previous run left behind.
    """
    from weave_core.graph.storage.neo4j import occupied_workspaces

    occupied = await occupied_workspaces()
    if not occupied:
        pytest.skip(
            "the configured Neo4j holds no workspace yet, so there is no "
            "occupancy for admission to conflict with (AS3 partially verified)"
        )

    holder = sorted(occupied)[0]
    await check_admission(holder, NEO4J)  # re-opening what exists is fine

    with pytest.raises(WorkspaceNotAdmitted):
        await check_admission("a_workspace_that_does_not_exist", NEO4J)
