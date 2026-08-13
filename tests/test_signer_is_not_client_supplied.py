"""The signer comes from the token, never from the body (A6, D-038).

**The most serious defect found in this project, and it was found late.** Six
unsigned write paths (D-032/033/034) meant governance changes went *unrecorded*.
This one is worse in kind: changes *were* recorded, **under whatever name the
caller typed**. A ledger that can be forged is worse than no ledger, because it
is trusted — "who took away my access" returns a confident, wrong answer.

Three surfaces carried it, all from the fork at `8610914`:

* ``POST /studio/apply``  — ``ApplyRequest.approver``
* ``POST /studio/revert`` — ``RevertRequest.approver`` *(not in the original
  report; found by checking the rest of the file rather than the one route)*
* ``save_diagram``        — an MCP tool **parameter**, so an agent could
  attribute a governance change to a person as easily as a human could

`weave-ui`'s Studio even rendered a text box for it, and validated only that the
name was **non-empty** — a check that made the illusion look like a guarantee,
then reported the forged name back as *"signed by …"*.

These tests are written to **fail against `2e6d90c`**. A test that only passes
after the fix proves the fix compiles; one that fails before it proves the fix
was needed.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ROUTERS = _ROOT / "weave" / "server" / "routers"


def _model_fields(source: str, class_name: str) -> set[str]:
    """Annotated field names of a pydantic model, read from the AST."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"{class_name} not found")


# ── 1 · the identity is not a field a caller can set ─────────────────────────


@pytest.mark.parametrize("model", ["ApplyRequest", "RevertRequest"])
def test_studio_request_models_carry_no_signer(model):
    """Deleted, not ignored.

    A field accepted and silently discarded is the next reader's bug: the API
    still advertises it, callers still send it, and nothing says it stopped
    working. Absence is the only version of this that stays true.
    """
    fields = _model_fields((_ROUTERS / "studio.py").read_text(encoding="utf-8"), model)
    assert "approver" not in fields, (
        f"{model} still accepts `approver` from the request body. A6: the "
        "principal is derived from the authenticated identity, never from a "
        "client-supplied field."
    )
    assert "role" not in fields, (
        f"{model} still accepts `role` — the role is what RBAC is enforced "
        "against, so a caller-supplied one is the same defect wearing a "
        "different name."
    )


def test_the_reason_is_still_the_callers_to_give():
    """The *reason* is legitimately the caller's — only the identity is not.
    Losing it in the fix would have traded one defect for another."""
    fields = _model_fields((_ROUTERS / "studio.py").read_text(encoding="utf-8"), "ApplyRequest")
    assert "reason" in fields


# ── 2 · the routes derive it, and refuse without one ─────────────────────────


@pytest.mark.parametrize("route", ["apply", "revert"])
def test_studio_routes_derive_the_signer_from_the_request(route):
    source = (_ROUTERS / "studio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == route
    )
    body = ast.get_source_segment(source, fn) or ""
    assert "_signer(" in body, f"/studio/{route} does not derive a signer"
    assert "body.approver" not in body, f"/studio/{route} still reads the body's approver"


def test_an_unauthenticated_signer_is_refused_not_left_blank():
    """401, not an empty name.

    A ledger entry signed by *nobody* is the same failure as one signed by
    *anybody* — both make the sign-off unable to answer the question it exists
    for. Copied from `routers/wizard.py`, which has always had this right.
    """
    source = (_ROUTERS / "studio.py").read_text(encoding="utf-8")
    signer = source[source.index("def _signer("):]
    assert "status_code=401" in signer[:1200]


# ── 3 · the same hole on the agent surface ───────────────────────────────────


def test_the_mcp_diagram_tool_takes_no_approver():
    """A9 cuts both ways: one handler serves REST and MCP, so a hole in one is a
    hole in both. This one was arguably worse — an agent could attribute a
    governance change to a *person*."""
    from weave.server import mcp as mcp_module

    source = pathlib.Path(mcp_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "save_diagram"),
        None,
    )
    assert fn is not None, "save_diagram is gone — check this test, not the code"
    params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    assert "approver" not in params, (
        "save_diagram still takes `approver` as a tool parameter — an agent can "
        "sign a governance change as anybody (A6, D-038)"
    )
    assert "reason" in params, "the reason is still legitimately the caller's"


def test_the_mcp_signature_says_what_is_actually_known():
    """Not a person's name, and not blank either.

    This MCP library exposes no request to derive an identity from — the same
    gap that leaves `principal_role=None` on the action path. So the signature
    records the one true thing: an agent did it, over MCP. Less than we want,
    and more than a name somebody typed.
    """
    from weave.server.mcp import MCP_AGENT_APPROVER

    assert MCP_AGENT_APPROVER
    assert MCP_AGENT_APPROVER != ""


# ── 4 · a caller's reason survives to the ledger ─────────────────────────────


@pytest.mark.parametrize("router,model", [
    ("ontology.py", "OntologyRequest"),
    ("rules.py", "RulePolicyRequest"),
])
def test_governance_editors_accept_a_reason(router, model):
    """These signed correctly but hardcoded `reason=f"… set by {approver}"`, so a
    UI collecting a reason would have discarded it — a screen promising an
    attribution it does not make."""
    fields = _model_fields((_ROUTERS / router).read_text(encoding="utf-8"), model)
    assert "reason" in fields, (
        f"{model} accepts no reason, so a caller's explanation cannot reach the "
        "ledger and any UI asking for one is lying about what it does"
    )


@pytest.mark.parametrize("router", ["ontology.py", "rules.py"])
def test_the_hardcoded_reason_survives_as_a_fallback(router):
    """Only as a fallback. Dropping it would leave a bare API call unattributed,
    which is the defect one layer down."""
    source = (_ROUTERS / router).read_text(encoding="utf-8")
    assert "request.reason.strip()" in source
    assert "set by {approver}" in source


# ── 5 · the class, so a fourth surface cannot appear quietly ─────────────────


def test_no_router_reads_a_signer_out_of_a_request_body():
    """The reach, asserted — the lesson this project keeps relearning.

    `/studio/revert` was not in the original finding; it was found by reading the
    rest of the file. A third instance would be found by nobody, so the rule runs
    over every router rather than the two that were reported.
    """
    offenders = []
    for path in sorted(_ROUTERS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "approver=body." in stripped or "approver=request.approver" in stripped:
                offenders.append(f"{path.name}:{i} — {stripped[:70]}")

    assert not offenders, (
        "a router takes the signer from the request body (A6, D-038):\n  "
        + "\n  ".join(offenders)
    )
