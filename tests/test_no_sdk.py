"""The subscription boundary, asserted rather than assumed (A13, R57, R58).

Weave has **two LLM paths and they never merge**:

* every Claude Code client — a human's seat and a dev container alike —
  authenticates by *subscription seat only*;
* the server's own model use (graph build, extraction, embedding, retrieval,
  rules) runs through the backend connectors, and that is the **only** place a
  model credential exists.

Subscription-based Claude Code access is a hard limitation of this architecture,
not a preference. An SDK call in the seat path does not fail loudly — it
succeeds, and meters work that was supposed to be covered by the seat. Nobody
notices until the bill.

So the SDK is not a dependency at all: absent from the manifest, imported by no
module, constructed by no code path. These tests are cheap and they are the only
thing standing between a plausible-looking import and a broken economic model.

The asymmetry in :func:`test_the_seat_variable_is_not_scrubbed` is deliberate and
is the one people get backwards (R70).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGES = ["weave", "weave_core"]

#: The forbidden SDK. Also the name of the vendor's HTTP API surface, which is
#: why the check below is by import statement rather than by substring.
SDK = "anthropic"


def _python_files():
    for pkg in PACKAGES:
        yield from (REPO / pkg).rglob("*.py")


@pytest.mark.offline
def test_no_module_imports_the_sdk():
    """Parsed, not grepped — a comment mentioning the SDK is not an import."""
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name.split(".")[0] == SDK for a in node.names):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == SDK:
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, (
        f"{SDK} is imported at {offenders}. A13: no SDK call may sit in a Claude "
        "Code path — it breaks the subscription model and meters the work."
    )


@pytest.mark.offline
def test_the_sdk_is_not_in_the_dependency_manifest():
    """`environment.yml` is the single dependency manifest (D-006, A11)."""
    manifest = (REPO / "environment.yml").read_text(encoding="utf-8")
    declared = [
        line.strip().lstrip("- ").split(">")[0].split("=")[0].split("<")[0].strip()
        for line in manifest.splitlines()
        if line.strip().startswith("- ") and not line.strip().startswith("- #")
    ]
    assert SDK not in declared, f"{SDK} is declared in environment.yml — A13 forbids it"


@pytest.mark.offline
def test_pyproject_declares_no_dependencies_of_its_own():
    """One manifest, or the two drift within a phase (R10)."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject, (
        "pyproject.toml has grown a dependency list; environment.yml is the one manifest"
    )


@pytest.mark.offline
def test_no_connector_module_survived_for_the_sdk():
    """The source shipped a connector module for it. It is not copied (D-015)."""
    llm = REPO / "weave_core" / "llm"
    assert not (llm / f"{SDK}.py").exists()
    names = sorted(p.stem for p in llm.glob("*.py") if p.stem != "__init__")
    assert names == [
        "azure_openai", "bedrock", "binding_options", "gemini", "jina",
        "lollms", "ollama", "openai", "rerank",
    ], f"the wired connector set changed: {names}"


@pytest.mark.offline
def test_the_scrub_list_covers_every_metered_variable():
    from weave.team.worker import SUBSCRIPTION_SCRUB_VARS

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert var in SUBSCRIPTION_SCRUB_VARS, f"{var} would reach a Claude Code process"


@pytest.mark.offline
def test_the_seat_variable_is_not_scrubbed():
    """The asymmetry people get backwards (R70).

    Scrubbing removes *metered* auth. The seat is the opposite of metered auth —
    scrub it and the whole fleet stops working, having "improved" security.
    """
    from weave.team.worker import SUBSCRIPTION_SCRUB_VARS

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in SUBSCRIPTION_SCRUB_VARS


@pytest.mark.offline
def test_scrubbing_removes_metered_auth_from_an_environment():
    from weave.team.worker import scrub_api_auth

    env = {
        "ANTHROPIC_API_KEY": "sk-should-not-survive",
        "ANTHROPIC_AUTH_TOKEN": "also-not",
        "ANTHROPIC_BASE_URL": "https://not-anthropic.example",
        "CLAUDE_CODE_OAUTH_TOKEN": "the-seat",
        "PATH": "/usr/bin",
    }
    cleaned = scrub_api_auth(dict(env))
    assert "ANTHROPIC_API_KEY" not in cleaned
    assert "ANTHROPIC_AUTH_TOKEN" not in cleaned
    assert "ANTHROPIC_BASE_URL" not in cleaned
    assert cleaned["CLAUDE_CODE_OAUTH_TOKEN"] == "the-seat"
    assert cleaned["PATH"] == "/usr/bin"
