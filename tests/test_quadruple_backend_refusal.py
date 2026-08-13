"""Quadruple mode on PostgreSQL is refused at startup, not discovered later (A4 v5, D-039).

**`deploy/compose.yml` ships a pair that has never worked on any commit.** It
sets `WEAVE_ENABLE_QUADRUPLE=true` and the four PostgreSQL backends; quadruple
mode creates `decisions` and `communities` vector stores unconditionally, and
`PGVectorStorage` has a table for neither — absent from `NAMESPACE_TABLE_MAP`,
no DDL, and `upsert` dispatches over three namespaces only.

Before this the server started, built storages until it reached one of the two,
and died on ``ValueError: Unknown namespace: decisions`` from deep inside the
engine — an error naming neither the backend, nor the mode, nor the fact that
the operator had followed the bundle's own defaults. In Docker that is a
crash-loop; the useful line scrolls past and the container restarts.

**The refusal is a stopgap that P9 deletes, not disables.** The tempting way to
close a gap like this is to widen the exception until the error stops, so these
tests pin the shape of the exception as much as its existence.
"""

from __future__ import annotations

import pytest

from weave_core.graph.storage import (
    QUADRUPLE_UNSUPPORTED_VECTOR_STORES,
    QuadrupleUnsupported,
    assert_quadruple_supported,
)

pytestmark = pytest.mark.offline


# ── the pair is refused ──────────────────────────────────────────────────────


def test_postgres_plus_quadruple_is_refused():
    """The bundle's exact configuration."""
    with pytest.raises(QuadrupleUnsupported):
        assert_quadruple_supported("PGVectorStorage", True)


def test_the_refusal_is_not_a_warning():
    """It raises rather than returning a flag a caller may ignore.

    A degraded mode here would be worse than the crash it replaces: the server
    would answer `/health`, look healthy, and fail on the first governed write —
    which is to say, the first time anyone did the thing Weave is for.
    """
    assert assert_quadruple_supported("NanoVectorDBStorage", True) is None
    with pytest.raises(QuadrupleUnsupported):
        assert_quadruple_supported("PGVectorStorage", True)


def test_a_silent_start_fails_this_test():
    """The criterion the manager asked for, stated as its own test.

    If someone deletes the call, softens it to a log line, or adds a bypass
    environment variable, this is what goes red — not a Docker run somebody has
    to remember to do.
    """
    started_silently = True
    try:
        assert_quadruple_supported("PGVectorStorage", True)
    except QuadrupleUnsupported:
        started_silently = False
    assert not started_silently, (
        "PGVectorStorage + quadruple mode was allowed to start. It cannot work: "
        "the decisions and communities vector stores have no PostgreSQL tables."
    )


# ── what is *not* refused ────────────────────────────────────────────────────


def test_postgres_without_quadruple_is_fine():
    """Retrieval on PostgreSQL works. The gap is governance mode, and refusing
    more than the gap would ship a smaller product than we have."""
    assert assert_quadruple_supported("PGVectorStorage", False) is None


def test_the_file_based_path_still_runs_quadruple():
    """The alternative the message offers has to actually work, or the refusal
    sends an operator somewhere with no exit."""
    assert assert_quadruple_supported("NanoVectorDBStorage", True) is None


def test_only_the_backend_that_lacks_the_tables_is_refused():
    """The map is a statement about a missing schema, not a policy about
    PostgreSQL. Widening it is how a stopgap becomes the design."""
    assert set(QUADRUPLE_UNSUPPORTED_VECTOR_STORES) == {"PGVectorStorage"}
    assert "NanoVectorDBStorage" not in QUADRUPLE_UNSUPPORTED_VECTOR_STORES


# ── the message earns its length ─────────────────────────────────────────────


def _message() -> str:
    with pytest.raises(QuadrupleUnsupported) as excinfo:
        assert_quadruple_supported("PGVectorStorage", True)
    return str(excinfo.value)


def test_the_refusal_names_both_missing_namespaces():
    """Both, by name. Naming one would send someone hunting for a single table
    and leave them with the same crash."""
    message = _message()
    assert "decisions" in message
    assert "communities" in message


def test_the_refusal_tells_the_operator_it_is_not_their_fault():
    """They followed `deploy/compose.yml`'s defaults. An error that reads as
    misconfiguration sends a careful person to re-check work that was correct."""
    message = _message()
    assert "not misconfigured" in message.lower() or "have not misconfigured" in message.lower()
    assert "compose.yml" in message


def test_the_refusal_offers_a_way_forward_and_says_what_it_costs():
    """Two exits, both honest: turning governance off, or the file-based store —
    which is single-operator only, and the message says so rather than
    recommending a path A4 restricts."""
    message = _message()
    assert "WEAVE_ENABLE_QUADRUPLE=false" in message
    assert "NanoVectorDBStorage" in message
    assert "single-operator" in message


def test_the_refusal_points_at_the_decision_and_the_phase():
    """So a reader can find out whether it is still true, rather than trusting a
    string in a binary."""
    message = _message()
    assert "D-039" in message
    assert "P9" in message


# ── the server refuses before it constructs anything ─────────────────────────


def test_the_check_runs_at_the_top_of_create_app():
    """Placement is the point.

    The failure this replaces happened *after* the workspace pool had been
    seeded and storages built, so the process had already done work and opened
    connections before it discovered it could not run. This sits with the
    signing-secret check, before any of that exists.
    """
    import inspect

    from weave.server.app import create_app

    source = inspect.getsource(create_app)
    head = source[: source.index("assert_quadruple_supported(") + 200]
    assert "assert_signing_secret_is_safe" in head, (
        "the quadruple check has drifted away from the other startup "
        "preconditions — it must run before anything is constructed"
    )
    assert "workspace_pool" not in head, (
        "the quadruple check now runs after the workspace pool is built; the "
        "whole point is that it fires before any storage exists"
    )
