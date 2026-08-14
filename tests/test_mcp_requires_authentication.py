"""`/mcp` authenticates exactly as REST does, and the tenant comes from the token (W33).

Measured on a running server with auth enabled and working:

    GET  /weave/status   no credentials → 401
    GET  /weave/tasks    no credentials → 401
    POST /mcp            no credentials → 200   ← and it executed

`tools/call get_manifest` returned a workspace's ontology and `ask_learnings` ran
the answer surface, **with the tenant taken from the `WEAVE-WORKSPACE` header on
that same unauthenticated request**. `app.mount("", mcp_app)` attaches a sub-app
outside `app.router.dependencies`, so the guard on every REST route simply was
not there.

**Against A6 directly** — *the principal it is enforced against is derived from
the authenticated identity … no endpoint bypasses either half* — and it is M2's
Critical in a new place: the tenant chosen by an unauthenticated client-supplied
field, on a surface the guide tells operators to bind to `0.0.0.0`.

**W16 saw half of it.** *"MCP carries no role"* was written down a week earlier
and read as a missing feature — `get_manifest` answered `"role": null` and RBAC
denied every agent on a governed workspace. It was the same absence: nothing
authenticated the request, so there was no role to enforce *and* no principal to
enforce the tenant against. The half that broke a feature was noticed; the half
that broke the boundary was not.

**`tools/list` is in the gate on purpose.** Discovery of a tenant's tool surface
is disclosure, and an unauthenticated caller learning the shape of the governed
API is the first half of using it.
"""

from __future__ import annotations

import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_MCP_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
_INITIALIZE = {"jsonrpc": "2.0", "id": 2, "method": "initialize",
               "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}}}


@pytest.fixture(scope="module")
def server():
    """A server with auth on, two users, and two workspaces they do not share."""
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        from weave.server.config import parse_args

        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = tempfile.mkdtemp(prefix="w33-")
    args.workers = 1
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    args.enable_weave = True
    args.use_quadruple = True   # get_manifest needs the governance engine

    from weave.server.users import JsonUserStore, UserService

    users = UserService(JsonUserStore(args.working_dir))
    users.create("alice", "a-good-password", workspaces=["alpha"], role="manager")
    users.create("dave", "a-good-password", workspaces=["beta"], role="developer")

    from weave.server.app import create_app
    from weave.server.auth import auth_handler

    # **Put the singleton back.** `create_app` calls
    # `auth_handler.bind_user_service(...)`, and `auth_handler` is process-wide,
    # so creating users here switches `auth_configured` on for every app another
    # module built at import time — 37 unrelated REST tests turned 401 the first
    # time this ran, while each still passed alone. A fixture that leaves the
    # world configured differently is a fixture that fails other people's tests.
    previous = auth_handler._users
    try:
        with TestClient(create_app(args)) as client:
            yield client
    finally:
        auth_handler.bind_user_service(previous)


def _token(server, username: str) -> str:
    response = server.post("/login",
                           data={"username": username, "password": "a-good-password"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _mcp(server, body, token: str | None = None, workspace: str | None = None):
    headers = dict(_MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if workspace:
        headers["WEAVE-WORKSPACE"] = workspace
    return server.post("/mcp", json=body, headers=headers)


# ── the boundary ─────────────────────────────────────────────────────────────


def test_rest_refuses_an_unauthenticated_request(server):
    """The comparison the defect was measured against — REST was always right."""
    assert server.get("/weave/status").status_code == 401
    assert server.get("/weave/tasks").status_code == 401


@pytest.mark.parametrize("body,name", [(_INITIALIZE, "initialize"),
                                       (_TOOLS_LIST, "tools/list")])
def test_mcp_refuses_an_unauthenticated_request(server, body, name):
    """**The gate.** Both verbs, because `tools/list` is disclosure on its own."""
    response = _mcp(server, body)
    assert response.status_code == 401, (
        f"POST /mcp {name} answered {response.status_code} with no credentials — "
        "REST answers 401 to the same request"
    )


def test_an_invalid_token_is_refused(server):
    assert _mcp(server, _TOOLS_LIST, token="not-a-real-token").status_code == 401


def test_a_workspace_header_alone_opens_nothing(server):
    """The tenant was selectable by header on an unauthenticated request."""
    for workspace in ("alpha", "beta", "default"):
        assert _mcp(server, _TOOLS_LIST, workspace=workspace).status_code == 401


# ── the tenant comes from the token ──────────────────────────────────────────


def test_a_member_reaches_their_own_workspace(server):
    """The positive case, so the refusals below are about membership rather than
    about the surface being broken."""
    assert _mcp(server, _TOOLS_LIST, token=_token(server, "alice"),
                workspace="alpha").status_code == 200
    assert _mcp(server, _TOOLS_LIST, token=_token(server, "dave"),
                workspace="beta").status_code == 200


@pytest.mark.parametrize("username,forbidden", [("alice", "beta"), ("dave", "alpha")])
def test_a_token_cannot_reach_a_workspace_it_does_not_hold(server, username, forbidden):
    """**The assertion M2 needed and did not have**, and the one to write weakest
    if written in a hurry: mint a token for A, ask for B, assert the refusal.

    Both directions, so this cannot pass because one workspace happens to be
    unreachable for an unrelated reason.
    """
    response = _mcp(server, _TOOLS_LIST, token=_token(server, username),
                    workspace=forbidden)
    assert response.status_code == 403, (
        f"{username} reached '{forbidden}' with a header — the WEAVE-WORKSPACE "
        "header selects among the workspaces a principal holds; it must never "
        "grant one"
    )


def test_the_refusal_says_which_boundary_was_crossed(server):
    """403, and a sentence that distinguishes *not yours* from *no credential*."""
    body = _mcp(server, _TOOLS_LIST, token=_token(server, "alice"),
                workspace="beta").json()
    assert "not a member" in body["detail"]
    assert "beta" in body["detail"]


# ── the rule itself, without a server ────────────────────────────────────────


@pytest.mark.parametrize("principal,workspace,allowed", [
    ({"workspaces": ["alpha"]}, "alpha", True),
    ({"workspaces": ["alpha"]}, "beta", False),
    ({"workspaces": []}, "alpha", False),
    ({"workspaces": None}, "alpha", True),      # guest / admin token, no grants
    ({}, "alpha", True),                        # no grants recorded at all
    (None, "alpha", True),                      # auth off, or an API-key caller
    ({"workspaces": ["alpha"]}, None, True),    # nothing resolved to check
])
def test_the_membership_rule(principal, workspace, allowed):
    """Deny only on a **positive mismatch**.

    Absence is not refusal: `combined_auth` has already decided whether the
    request may proceed, and a second veto in a second place is how two rules
    drift apart. What is refused is the case a client-supplied header could
    otherwise satisfy on its own — a token that names its workspaces, asking for
    one it does not name.
    """
    from weave.server.utils import principal_may_access

    assert principal_may_access(principal, workspace) is allowed


def test_the_principal_carries_its_grants_from_the_token():
    """Server-derived, like the role. A grant read from a header would be the
    self-stamped principal A6 exists to forbid."""
    import inspect

    from weave.server.utils import get_principal

    source = inspect.getsource(get_principal)
    assert '"workspaces"' in source
    assert "metadata" in source, (
        "the grant no longer comes from the validated token's metadata"
    )


# ── W16 closes with it ───────────────────────────────────────────────────────


def _call(server, tool: str, token: str, workspace: str, arguments=None):
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}}}
    return _mcp(server, body, token=token, workspace=workspace)


def test_the_manifest_answers_as_the_caller(server):
    """**W16, closed by the same change.**

    It was recorded as *"MCP tools RBAC-check with role `None`, fails closed → an
    RBAC-enabled workspace denies every MCP agent"*, and `get_manifest` returned
    `"role": null`. That was never a separate defect: the request carried no
    authenticated principal, so there was no role to answer with **and** nothing
    to enforce the tenant against. Authenticating the surface produces both.

    Asserted for two different roles, because a manifest that answered "manager"
    for everyone would pass a single-role check while meaning nothing.
    """
    for username, role in (("alice", "manager"), ("dave", "developer")):
        workspace = "alpha" if username == "alice" else "beta"
        response = _call(server, "get_manifest", _token(server, username), workspace)
        assert response.status_code == 200, response.text
        assert f'"{role}"' in response.text or role in response.text, (
            f"the manifest did not answer as {role} — MCP still carries no role"
        )


def test_the_tools_read_the_role_from_the_principal_not_a_parameter():
    """A role a caller passes is the self-stamped principal A6 forbids. The
    parameter survives as a *narrowing* filter; the default is who you are."""
    import ast
    import pathlib as _pathlib

    from weave.server import mcp as mcp_module

    # **Parsed, not grepped** — the fifth matcher in this project to flag the
    # comment that *explains* a fix as though it were the fix's absence. The
    # history of `principal_role=None` is written down two hundred lines from
    # the call site that no longer does it.
    tree = ast.parse(_pathlib.Path(mcp_module.__file__).read_text(encoding="utf-8"))
    passed = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "principal_role":
                passed.append(ast.unparse(keyword.value))
    assert passed, "nothing passes a principal_role at all any more"
    assert "None" not in passed, (
        f"an MCP tool invokes governance with no principal: principal_role={passed}"
    )
