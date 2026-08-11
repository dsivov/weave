"""Which workspaces a storage path is allowed to hold (A4 v4, D-029).

The three storage paths are not interchangeable, and the contract says which is
which. PostgreSQL is the multi-workspace production path. The file path is
single-operator. **The Neo4j path is experimental and single-workspace**, and
that restriction is enforced here rather than documented.

**Why it is code and not prose.** Neo4j Community has no multi-database support
— it is an Enterprise feature — so every workspace on that path shares one
database, separated only by a label. The M1 review proposed qualifying A4 to say
so; dsivov chose the narrower promise instead, because a qualification annotates
the failure but leaves it available: an operator who reads the caveat and
proceeds anyway gets no error, just silent co-tenancy. That is the same shape as
the in-process bus under multiple workers (D-019), and D-029 exists to close it.

**Where the check goes, and why it is not in the adapter.** It runs at the point
a workspace is *created* — `WorkspacePool`, since a workspace on this system
comes into being when one is first requested. Not in the adapter, which would
make it a per-operation cost and would refuse reads of data that already exists;
and not at read time, which is far too late to be useful. The adapter's only job
is to answer *which workspaces the database already holds*, because A4 also says
no module constructs a database client outside its own adapter.

Reading the answer from the database rather than from in-process bookkeeping is
what makes the refusal survive a restart. A guard that a restart defeats is the
documented-only restriction again, wearing a different hat.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Optional, Set

from weave_core.utils import logger

#: Graph backends that can hold exactly one workspace, and why.
#:
#: Keyed by the storage class name the configuration selects
#: (`WEAVE_GRAPH_STORAGE`), so adding a backend to this set is a deliberate act
#: with a stated reason rather than a string comparison buried in a branch.
SINGLE_WORKSPACE_GRAPH_BACKENDS = {
    "Neo4JStorage": (
        "Neo4j Community Edition cannot give a workspace its own database, so "
        "every workspace would share one and be separated only by a label. The "
        "Neo4j path is supported for a single workspace only (A4, D-029)."
    ),
}


class WorkspaceNotAdmitted(Exception):
    """This backend will not hold another workspace.

    Carries an actionable message: which workspace already occupies the backend,
    why a second is refused, and what to move to.
    """


async def occupied_workspaces(graph_storage: str) -> Optional[Set[str]]:
    """Workspaces the configured graph backend already holds.

    Delegates to the adapter — see the module docstring on A4. Backends with no
    single-workspace limit are never asked, so an unreachable Neo4j cannot
    refuse anything on a deployment that does not use Neo4j.

    **`None` means occupancy could not be determined** and is not the same
    answer as an empty set.
    """
    if graph_storage not in SINGLE_WORKSPACE_GRAPH_BACKENDS:
        return set()
    if graph_storage == "Neo4JStorage":
        from weave_core.graph.storage.neo4j import occupied_workspaces as probe

        return await probe()
    return set()


async def check_admission(
    workspace: str,
    graph_storage: str,
    *,
    known_workspaces: Iterable[str] = (),
    probe: Optional[Callable[[], Awaitable[Optional[Set[str]]]]] = None,
) -> None:
    """Raise :class:`WorkspaceNotAdmitted` if *workspace* may not be created.

    `known_workspaces` is what this process has already built — cheap, and
    authoritative for anything created since boot. The `probe` supplies what the
    database itself holds, which is what makes the answer correct across a
    restart. The union of the two is the occupancy.

    A workspace that is *already* occupying the backend is always admitted:
    re-opening the workspace you have is not creating a second one.

    **Undetermined occupancy fails closed** (M2 review, H1). When the probe
    cannot answer — unreachable database, auth failure, a query error — a *new*
    workspace is refused. The earlier version admitted it, because a failed
    probe returned an empty set and "could not ask" was indistinguishable from
    "holds nothing"; a restart during a momentary Neo4j outage would then admit
    a second workspace permanently and silently, which is the exact failure
    D-029 replaced prose with code to prevent.

    The trade is deliberate and narrow: what is refused is the one irreversible
    act. A workspace already being served keeps being served, every current
    caller is unaffected, and nothing about reads changes. Fail-open is right
    for reads and wrong for admission.
    """
    reason = SINGLE_WORKSPACE_GRAPH_BACKENDS.get(graph_storage)
    if reason is None:
        return

    occupied = {w for w in known_workspaces if w}
    probed = await (probe() if probe is not None else occupied_workspaces(graph_storage))

    undetermined = probed is None
    if not undetermined:
        occupied |= probed

    # Re-opening a workspace we know about is never "creating a second one",
    # so it is admitted even when the database could not be reached.
    if workspace in occupied:
        return

    if undetermined:
        raise WorkspaceNotAdmitted(
            f"Cannot create workspace '{workspace}': this deployment uses "
            f"{graph_storage}, and its current occupancy could not be verified "
            "— the database could not be reached or did not answer. "
            f"{reason} Admitting a workspace without checking is how two of them "
            "end up sharing one database silently, so this is refused rather "
            "than guessed. Restore connectivity and retry; workspaces already "
            "running are unaffected."
        )

    if not occupied:
        return

    holder = sorted(occupied)[0]
    raise WorkspaceNotAdmitted(
        f"Cannot create workspace '{workspace}': this deployment uses "
        f"{graph_storage}, which already holds workspace '{holder}'. {reason} "
        "Use the PostgreSQL graph backend for a multi-workspace deployment "
        "(WEAVE_GRAPH_STORAGE=PGGraphStorage), or run this workspace on its own "
        "Neo4j instance."
    )


def log_policy(graph_storage: str) -> None:
    """Say once, at startup, that this deployment is single-workspace.

    An operator who learns the limit from a refused request learns it at the
    worst possible moment.
    """
    reason = SINGLE_WORKSPACE_GRAPH_BACKENDS.get(graph_storage)
    if reason:
        logger.warning(
            f"{graph_storage} is a single-workspace graph backend — a second "
            f"workspace will be refused. {reason}"
        )
