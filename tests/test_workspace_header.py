"""The workspace header is the tenant selector, and it has to actually match.

`WEAVE-WORKSPACE` is the published contract: it is in the OpenAPI document
(`app.py`), the UI client, the dev worker, the playbook and six router
docstrings. The ASGI middleware in `weave/server/workspace_pool.py` is the
**only** place `_current_workspace` is ever set, so if its header lookup misses,
every request in the system resolves to the default workspace — silently, with
no error and no log. Membership grants then decide nothing, because the thing
they scope was never selected.

That is what a rebrand sed left behind: the middleware looked for
`weave_core-workspace`, a name no client has ever sent.

Two properties are asserted here, and the second is the one that would have
caught it:

1. the middleware resolves the workspace from the **documented** header, in any
   case a client might send it in (HTTP header names are case-insensitive, and
   ASGI hands them to us lowercased in `scope["headers"]` — a raw-scope read
   that compares against anything but a lowercase literal can never match);
2. **two workspaces, over HTTP, see different data.** M1 asserted tenancy at the
   token/membership layer and passed; this asserts it at the layer that answers
   the request.

A third test pins the *class* rather than the line: any future raw ASGI header
read must compare against a lowercase byte literal.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from weave.server.routers.ontology import create_ontology_routes
from weave.server.workspace_pool import (
    WORKSPACE_HEADER,
    _WORKSPACE_HEADER_BYTES,
    _current_workspace,
    get_workspace_middleware,
)
from weave_core.governance.ontology import InMemoryOntologyStore, OntologyService


# The header every client in this repository actually sends. Written out
# literally here on purpose: if the constant under test is ever changed, this
# test must fail rather than follow it.
DOCUMENTED_HEADER = "WEAVE-WORKSPACE"


class _StubPool:
    """Enough of a WorkspacePool for the middleware: it only awaits get_rag."""

    def __init__(self) -> None:
        self.asked_for: list[str] = []

    async def get_rag(self, workspace: str):
        self.asked_for.append(workspace)
        return object()


def _echo_app(default_workspace: str = "default"):
    """An app whose single route reports the workspace the middleware selected."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami():
        return {"workspace": _current_workspace.get()}

    pool = _StubPool()
    app.add_middleware(get_workspace_middleware(pool, default_workspace))
    return app, pool


# ── 1 · the middleware honours the documented header ─────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize(
    "sent_as",
    ["WEAVE-WORKSPACE", "weave-workspace", "Weave-Workspace", "wEaVe-WoRkSpAcE"],
    ids=["upper", "lower", "title", "mixed"],
)
def test_middleware_resolves_the_documented_header(sent_as):
    """Any casing a client sends selects the workspace it names.

    Parameterised on casing deliberately: the defect was a lookup that could
    never match, and a single-casing test would not distinguish "matches the
    documented name" from "matches the one string I happened to send".
    """
    app, pool = _echo_app()
    with TestClient(app) as client:
        body = client.get("/whoami", headers={sent_as: "alpha"}).json()

    assert body["workspace"] == "alpha", (
        f"header sent as {sent_as!r} did not select workspace 'alpha' — "
        f"got {body['workspace']!r}; the middleware is not reading the "
        f"{DOCUMENTED_HEADER} header the whole system publishes"
    )
    assert pool.asked_for == ["alpha"], (
        "the pool was asked for the wrong workspace, so storage would have "
        f"been initialised for the wrong tenant: {pool.asked_for}"
    )


@pytest.mark.offline
def test_no_header_falls_back_to_the_server_default():
    app, _ = _echo_app(default_workspace="house")
    with TestClient(app) as client:
        assert client.get("/whoami").json()["workspace"] == "house"


@pytest.mark.offline
def test_an_invalid_workspace_name_is_refused_not_defaulted():
    """A bad name must 400. Falling back to the default would be the same
    silent-wrong-tenant failure wearing different clothes."""
    app, pool = _echo_app()
    with TestClient(app) as client:
        r = client.get("/whoami", headers={DOCUMENTED_HEADER: "alpha/../beta"})

    assert r.status_code == 400
    assert pool.asked_for == [], "an invalid workspace was initialised anyway"


# ── 2 · two workspaces see different data, over HTTP ─────────────────────────


ALPHA_ONTOLOGY = {
    "name": "alpha-vocabulary",
    "object_types": [{"name": "Widget", "properties": []}],
    "link_types": [],
}
BETA_ONTOLOGY = {
    "name": "beta-vocabulary",
    "object_types": [{"name": "Sprocket", "properties": []}],
    "link_types": [],
}


class _Rag:
    """The router's `_require_cg` only checks for a `rules_gate` attribute."""

    rules_gate = object()


@pytest.mark.offline
def test_two_workspaces_see_different_data_over_http():
    """The assertion M1 was missing.

    M1 verified that a user *sees only granted workspaces* at the membership
    layer and passed — while every HTTP request was in fact being answered out
    of the default workspace. Grants scoping a selection that never happened is
    not a tenant boundary. So: write two different ontologies into two
    workspaces through the real service, then ask over HTTP and require the
    answers to differ.
    """
    service = OntologyService(InMemoryOntologyStore(now=lambda: 1.0))
    service.save("alpha", ALPHA_ONTOLOGY)
    service.save("beta", BETA_ONTOLOGY)

    app = FastAPI()
    app.include_router(create_ontology_routes(_Rag(), service))
    app.add_middleware(get_workspace_middleware(_StubPool(), "default"))

    with TestClient(app) as client:
        alpha = client.get("/ontology", headers={DOCUMENTED_HEADER: "alpha"}).json()
        beta = client.get("/ontology", headers={DOCUMENTED_HEADER: "beta"}).json()
        unnamed = client.get("/ontology").json()

    assert alpha["workspace"] == "alpha" and beta["workspace"] == "beta"
    assert alpha["name"] == "alpha-vocabulary"
    assert beta["name"] == "beta-vocabulary", (
        "both workspaces answered with the same data — the tenant boundary "
        "collapsed to a single workspace"
    )
    assert {o["name"] for o in alpha["object_types"]} == {"Widget"}
    assert {o["name"] for o in beta["object_types"]} == {"Sprocket"}

    # The default workspace has no ontology; if the header were being ignored,
    # this is the answer *both* calls above would have returned.
    assert unnamed["workspace"] == "default" and unnamed["exists"] is False


@pytest.mark.offline
def test_two_workspaces_stay_separate_through_a_real_persisting_store(tmp_path):
    """The same assertion again, but nothing is held in memory.

    `InMemoryOntologyStore` keys off the workspace argument, so the test above
    would hold even if persistence were workspace-blind. `JsonOntologyStore`
    writes one file per workspace, so this version also proves the workspace
    reaches the layer that decides *where bytes land* — and it survives a fresh
    service instance, which an in-memory dict cannot show.

    The database-backed version of this belongs with `/projects/resolve`
    (R22a), where the tenant boundary is load-bearing and the store is the
    RecordStore port; it is asserted in `test_project_layout_tenancy.py`.
    """
    from weave_core.governance.ontology import JsonOntologyStore

    writer = OntologyService(JsonOntologyStore(str(tmp_path)))
    writer.save("alpha", ALPHA_ONTOLOGY)
    writer.save("beta", BETA_ONTOLOGY)

    # A second service over the same directory — nothing carried in process.
    app = FastAPI()
    app.include_router(
        create_ontology_routes(_Rag(), OntologyService(JsonOntologyStore(str(tmp_path))))
    )
    app.add_middleware(get_workspace_middleware(_StubPool(), "default"))

    with TestClient(app) as client:
        alpha = client.get("/ontology", headers={DOCUMENTED_HEADER: "alpha"}).json()
        beta = client.get("/ontology", headers={DOCUMENTED_HEADER: "beta"}).json()

    assert alpha["name"] == "alpha-vocabulary"
    assert beta["name"] == "beta-vocabulary"
    assert {o["name"] for o in alpha["object_types"]} == {"Widget"}
    assert {o["name"] for o in beta["object_types"]} == {"Sprocket"}

    # And the separation is visible on disk, not only in the response.
    written = sorted(p.name for p in tmp_path.glob("ontology_*.json"))
    assert written == ["ontology_alpha.json", "ontology_beta.json"]


# ── 3 · the class, not the line ──────────────────────────────────────────────


_REPO = pathlib.Path(__file__).resolve().parent.parent

# `headers.get(b"...")` / `headers[b"..."]` against a raw ASGI scope dict.
_RAW_HEADER_READ = re.compile(rb"""headers\s*(?:\.get\(|\[)\s*b(['"])([^'"]+)\1""")


@pytest.mark.offline
def test_the_published_header_is_the_header_the_middleware_reads():
    """The assertion that actually pins *this* defect's class.

    The bug was not a casing mistake — `weave_core-workspace` was already
    lowercase, so a casing rule would have passed it. The bug was a **second
    copy** of a name whose other side lives outside this codebase, in every HTTP
    client that talks to us. A renamed literal is safe when both sides were
    renamed together and broken when they were not, and a header can never be
    renamed on both sides at once.

    So the invariant is agreement, not spelling: what the OpenAPI document
    publishes and what the middleware reads must be the same string.
    """
    published = _openapi_workspace_header_name()
    assert published == WORKSPACE_HEADER, (
        f"OpenAPI publishes {published!r} but the middleware reads "
        f"{WORKSPACE_HEADER!r}; a client following the documentation would be "
        "silently served the default workspace"
    )
    assert _WORKSPACE_HEADER_BYTES == WORKSPACE_HEADER.lower().encode("latin-1")

    # …and no module may carry a second copy of the name. This is the assertion
    # with teeth: the probe above only proves the constant survives FastAPI's
    # alias machinery, whereas a duplicated literal elsewhere would sail past it
    # and reintroduce exactly the drift that caused the outage.
    duplicates: list[str] = []
    for path in sorted(_REPO.glob("weave/**/*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == "workspace_pool.py":
            continue  # the one legitimate definition
        for lineno in _string_constant_lines(path, WORKSPACE_HEADER):
            duplicates.append(f"{path.relative_to(_REPO)}:{lineno}")

    assert not duplicates, (
        f"{WORKSPACE_HEADER!r} is defined once, in workspace_pool.py — import "
        "WORKSPACE_HEADER instead of writing it out again:\n  "
        + "\n  ".join(duplicates)
    )


def _string_constant_lines(path: pathlib.Path, value: str) -> list[int]:
    """Lines where *value* appears as a string constant the code actually uses.

    Parsed rather than grepped so that documentation is left alone: a docstring
    naming the header is exactly what we want people to write, while a second
    *usable* copy of it is the defect. Docstrings are `Expr(Constant)`
    statements, so they are identifiable and skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value == value
        and id(node) not in docstring_nodes
    ]


def _openapi_workspace_header_name() -> str:
    """The workspace header name as the generated OpenAPI document declares it.

    Read out of the real generated document rather than the source, because the
    document is the contract a client is entitled to rely on.
    """
    from fastapi import Depends, FastAPI, Header

    app = FastAPI()

    async def workspace_header_doc(
        weave_workspace: str = Header(default="default", alias=WORKSPACE_HEADER),
    ):
        pass

    # Appended before the route is registered: FastAPI binds router-level
    # dependencies at registration time, so the order matters here.
    app.router.dependencies.append(Depends(workspace_header_doc))

    @app.get("/probe")
    async def probe():
        return {}

    params = app.openapi()["paths"]["/probe"]["get"]["parameters"]
    header_params = [p["name"] for p in params if p["in"] == "header"]
    assert len(header_params) == 1, header_params
    return header_params[0]


@pytest.mark.offline
def test_raw_asgi_header_reads_use_lowercase_literals():
    """ASGI delivers header names lowercased in ``scope["headers"]``.

    A raw-scope read compared against a mixed- or upper-case byte literal can
    never match, and it fails *silently* — the caller sees a default, not an
    error. Neither the name-guard nor a type checker catches it: the string is
    well-formed and correctly spelled. So the rule is asserted here for the
    whole tree, not just for the one site that was wrong.
    """
    offenders: list[str] = []
    for path in sorted(_REPO.glob("weave*/**/*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        source = path.read_bytes()
        for match in _RAW_HEADER_READ.finditer(source):
            name = match.group(2).decode("latin-1")
            if name != name.lower():
                line = source[: match.start()].count(b"\n") + 1
                offenders.append(f"{path.relative_to(_REPO)}:{line} — b'{name}'")

    assert not offenders, (
        "raw ASGI header reads must use a lowercase byte literal, or they "
        "silently never match:\n  " + "\n  ".join(offenders)
    )
