"""No workspace-scoped route answers for a workspace the principal does not hold (W34).

Measured on current code, with a real user:

    carol — member of 'demo' only

      REST /weave/status   WEAVE-WORKSPACE: demo           200   correct
      REST /weave/status   WEAVE-WORKSPACE: default        200   ← not hers
      REST /weave/status   WEAVE-WORKSPACE: someone_elses  200   ← did not exist;
                                                                  created for her

**`User.may_access` has existed since P1**, and `tests/test_membership.py`
asserts it — against the *store*. That is the same shape as
`test_all_three_graph_adapters_import`: a test that proves the unit is correct
and never proves anyone calls it. Both were found in the same week, and both were
hiding something that had never run.

A4 calls the workspace the tenant boundary and A14 makes membership explicit. On
REST it was neither. It is M2's Critical in the same shape — the tenant taken
from a client-supplied header without checking the principal may have it. M2
fixed how that header *resolves*; nobody checked the *authorisation* of it.

**Why this file enumerates the route table instead of testing the helper.** A
single test proving `principal_may_access` works is exactly the trap that let
this live since P1. The property is *no workspace-scoped route answers for a
workspace the principal is not a member of*, so the routes are read from the
running application and driven one by one — and a route added next year is an
offender here until somebody says which it is.
"""

from __future__ import annotations

import re
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

#: Routes that are **not** workspace-scoped, each with the reason.
#:
#: Declared rather than inferred, and deliberately short. Nothing distinguishes
#: "global route" from "route whose scoping we forgot" by inspection, so the
#: default is *scoped*: an unlisted route must refuse a non-member, and a new one
#: fails this test until someone decides which it is. That default is the whole
#: point — the inverse list is how a boundary quietly stops covering things.
NOT_WORKSPACE_SCOPED = {
    "/login": "issues the token; there is no principal yet",
    "/auth-status": "asks whether auth is configured at all, before signing in",
    "/health": "liveness for a load balancer, and whitelisted",
    "/docs": "the API documentation page",
    "/openapi.json": "the API document itself",
    "/redoc": "the API documentation page",
    # Account administration is global: an administrator manages people, and
    # people are not owned by a workspace. Membership *grants* are edited
    # through here, so scoping it to a workspace would make the boundary
    # self-referential.
    "/users": "global account administration",
    "/workspaces": "lists and creates the workspaces themselves",
    "/": "the UI root; it serves assets, not a tenant's data",
    # **Found by this test, and worth a second look rather than an exemption.**
    # It carries no auth dependency at all — the guide tells an operator to
    # `curl -s …/workspace/backfill-script | python -`, so being reachable is
    # probably deliberate. It returns a fixed script and reads no workspace, so
    # it is not a tenant boundary question; that it is unauthenticated is a
    # separate one, reported rather than decided here.
    "/workspace/backfill-script": "serves a fixed script, and is public by design",
}


@pytest.fixture(scope="module")
def server():
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        from weave.server.config import parse_args

        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = tempfile.mkdtemp(prefix="w34-")
    args.workers = 1
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    args.enable_weave = True
    args.use_quadruple = True

    from weave.server.users import JsonUserStore, UserService

    UserService(JsonUserStore(args.working_dir)).create(
        "carol", "a-good-password", workspaces=["demo"], role="manager")

    from weave.server.app import create_app
    from weave.server.auth import auth_handler

    previous = auth_handler._users
    try:
        with TestClient(create_app(args)) as client:
            yield client
    finally:
        auth_handler.bind_user_service(previous)


@pytest.fixture(scope="module")
def carol(server) -> str:
    response = server.post("/login",
                           data={"username": "carol", "password": "a-good-password"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _scoped_get_paths(server) -> list[str]:
    """Every GET path the running app publishes, minus the declared exemptions."""
    document = server.app.openapi()
    paths = []
    for path, verbs in document["paths"].items():
        if "get" not in verbs:
            continue
        top = "/" + path.strip("/").split("/")[0]
        if path in NOT_WORKSPACE_SCOPED or top in NOT_WORKSPACE_SCOPED:
            continue
        if path in CANNOT_BE_DRIVEN:
            continue
        paths.append(path)
    return sorted(paths)


def _drive(path: str) -> str:
    """A concrete URL. Path parameters get a placeholder — the auth dependency
    runs before the handler, so whether the object exists is irrelevant to what
    is being asserted."""
    return re.sub(r"\{[^}]+\}", "x", path)


#: Routes that cannot be *driven* from a test client — they stream (SSE) or hold
#: a connection open. They are still workspace-scoped and still covered: the
#: structural test below asserts every route carries the auth dependency, which
#: is where the boundary lives.
#:
#: **They cannot be driven with a timeout, either.** `TestClient` invokes the
#: ASGI app in-process rather than over a socket, so an httpx read timeout never
#: fires — the first two versions of this file simply hung, once for ten minutes.
#: A bound that does not bind is worse than no bound, because it reads as one.
CANNOT_BE_DRIVEN = {
    "/live/stream": "server-sent events; the connection stays open by design",
    "/live/presence": "long-polls for presence changes",
}


def _ask(server, path: str, token: str, workspace: str) -> int:
    """Drive one route and return its status code."""
    return server.get(
        _drive(path),
        params={"timeout": 0},   # the long-poll routes take this and return at once
        headers={"Authorization": f"Bearer {token}", "WEAVE-WORKSPACE": workspace},
    ).status_code


def test_there_are_routes_to_check(server):
    """The premise, before anything is concluded from it. A filter that silently
    matched everything would make every assertion below vacuous."""
    paths = _scoped_get_paths(server)
    assert len(paths) > 40, f"only {len(paths)} workspace-scoped GET routes found"


def test_no_scoped_route_answers_for_a_workspace_the_caller_lacks(server, carol):
    """**The property, over the whole surface.**

    Driven with a valid token — so a refusal here is about *membership*, not
    about credentials — naming a workspace the principal was never granted.
    """
    offenders = []
    for path in _scoped_get_paths(server):
        status = _ask(server, path, carol, "not_carols_workspace")
        if status != 403:
            offenders.append(f"{path} → {status}")
    assert not offenders, (
        "these routes answered for a workspace the caller is not a member of:\n  "
        + "\n  ".join(offenders)
        + "\n\n  Either the route is workspace-scoped and must refuse, or it is "
        "global and belongs in NOT_WORKSPACE_SCOPED with the reason."
    )


def test_the_same_routes_answer_for_the_workspace_she_does_hold(server, carol):
    """**The other half, and the one that makes the test above mean something.**

    A boundary that refuses everything is not a boundary. If these were failing
    for an unrelated reason — a missing service, a 500 — the test above would
    pass while proving nothing.
    """
    refused = []
    for path in _scoped_get_paths(server):
        if _ask(server, path, carol, "demo") == 403:
            refused.append(path)
    assert not refused, (
        "these routes refused the caller in her own workspace:\n  "
        + "\n  ".join(refused)
    )


def test_a_workspace_that_does_not_exist_is_not_created_for_a_non_member(server, carol):
    """The sharpest form of the original defect: naming an unknown workspace did
    not merely read one, it **brought one into being** — the pool initialises
    whatever the header says."""
    assert _ask(server, "/weave/status", carol, "conjured_out_of_a_header") == 403


def test_the_exemptions_are_few_and_each_says_why(server):
    """An inverse list is where a boundary goes to stop covering things. It is
    asserted small so that widening it is a visible act."""
    assert len(NOT_WORKSPACE_SCOPED) <= 10, (
        "the not-scoped list is growing; a boundary with many exceptions is a "
        "boundary nobody can state"
    )
    for path, reason in NOT_WORKSPACE_SCOPED.items():
        assert reason and len(reason) > 15, f"{path} is exempt with no real reason"


def test_the_rule_lives_in_the_auth_dependency_not_in_each_route():
    """One check, where every authenticated route already passes.

    Per-route enforcement is a rule a new route can be added without — which is
    how `may_access` came to exist for four phases without a caller. And the MCP
    mount deliberately no longer carries its own copy: it was correct for a day,
    and two answers to "may this principal address this workspace" is the shape
    of defect this whole sequence has been about.
    """
    import inspect

    from weave.server import app as app_module
    from weave.server.utils import get_combined_auth_dependency

    assert "principal_may_access" in inspect.getsource(get_combined_auth_dependency)
    assert "principal_may_access" not in inspect.getsource(app_module._mcp_behind_auth), (
        "the MCP mount has grown a second copy of the membership rule"
    )
