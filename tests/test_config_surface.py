"""No instruction names a variable that nothing reads (R7, D-024).

This is the class of defect neither guard covers, and M0's review found one.
`scripts/nameguard.sh` looks for the *source product's* names; A3 v3 extends
that to generated contracts. Neither notices a **correct-looking old variable
name** — a string that is not branded, not misspelled, and simply obsolete.

It fails in the worst way available. An operator reads "set X for production",
sets X, and nothing reads it: the insecure default stays live while the warning
that would have told them fires on into a log they have already actioned. And in
`STORAGE_ENV_REQUIREMENTS` the same slip is not advice at all — those names are
read out of ``os.environ`` at engine start, so a stale one **rejects a correctly
configured deployment** and blames the operator for it.

Two rules, both mechanical:

* every variable Weave itself reads is ``WEAVE_``-prefixed;
* every variable a *vendor library* reads is not, and is listed here by name.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Read by a third-party library itself — prefixing these breaks the library.
#: Any addition here should be traceable to a library that reads the name.
VENDOR = {
    "OPENAI_API_KEY", "OPENAI_API_VERSION",
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
    "OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "GUNICORN_CMD_ARGS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
}

#: All-caps identifiers that are not environment variables at all.
NOT_ENV = {
    # storage *roles* in the registry, not variables
    "KV_STORAGE", "VECTOR_STORAGE", "GRAPH_STORAGE", "DOC_STATUS_STORAGE",
    # namespaces
    "DOC_STATUS", "FULL_DOCS", "TEXT_CHUNKS", "FULL_ENTITIES", "FULL_RELATIONS",
    "ENTITY_CHUNKS", "RELATION_CHUNKS", "LLM_RESPONSE_CACHE",
    # stdlib / third-party constants
    "VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NOTSET",
    "SUPPRESS", "DEFAULT", "SSL_MODE",
}

ENV_SENTENCE = re.compile(
    r"""(?P<name>[A-Z][A-Z0-9_]{4,})       # an all-caps identifier
        (?=[^\n]{0,60}?                    # …followed close by, on the same line,
           (?:env\s+var|environment\s+variable|environment\s+var))""",
    re.X,
)


def _sources():
    for pkg in ("weave", "weave_core"):
        for p in (REPO / pkg).rglob("*.py"):
            if "webui" in p.parts or "__pycache__" in p.parts:
                continue
            yield p


@pytest.mark.offline
def test_no_message_instructs_an_operator_to_set_an_unread_variable():
    """THE M0 REVIEW FINDING (H1), generalised.

    Any text that tells someone to set an environment variable must name one
    that exists. A prose instruction is a contract with a human, and a stale one
    costs them a debugging session and leaves the thing they fixed unfixed.
    """
    offenders = []
    for path in _sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in ENV_SENTENCE.finditer(line):
                name = m.group("name")
                if name in VENDOR or name in NOT_ENV or name.startswith("WEAVE_"):
                    continue
                offenders.append(f"{path.relative_to(REPO)}:{lineno} names {name}")
    assert not offenders, (
        "these instructions name a variable nothing reads:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.offline
def test_storage_env_requirements_are_variables_configuration_actually_writes():
    """Not a label — `check_storage_env_vars()` reads each of these from the
    environment at engine start and refuses to start when one is missing. A
    stale name here rejects a correct deployment and blames the operator."""
    from weave_core.graph.storage import STORAGE_ENV_REQUIREMENTS

    config = (REPO / "weave" / "server" / "config.py").read_text(encoding="utf-8")
    postgres = (REPO / "weave_core" / "graph" / "storage" / "postgres.py").read_text(encoding="utf-8")
    neo4j = (REPO / "weave_core" / "graph" / "storage" / "neo4j.py").read_text(encoding="utf-8")
    surface = config + postgres + neo4j

    for impl, required in STORAGE_ENV_REQUIREMENTS.items():
        for var in required:
            assert var.startswith("WEAVE_"), (
                f"{impl} requires {var}, which D-024 renamed — the engine would "
                f"refuse to start on a correctly configured deployment"
            )
            assert f'"{var}"' in surface, (
                f"{impl} requires {var}, which nothing in the configuration or the "
                f"storage adapters ever reads"
            )


@pytest.mark.offline
def test_no_configuration_variable_weave_reads_is_left_unprefixed():
    """D-024, swept over the whole configuration module."""
    config = (REPO / "weave" / "server" / "config.py").read_text(encoding="utf-8")
    reads = set(re.findall(r'get_env_value\(\s*"([A-Z][A-Z0-9_]*)"', config))
    reads |= set(re.findall(r'os\.(?:environ\.get|getenv)\(\s*"([A-Z][A-Z0-9_]*)"', config))
    stale = sorted(n for n in reads if not n.startswith("WEAVE_") and n not in VENDOR)
    assert not stale, f"configuration still reads unprefixed variables: {stale}"


@pytest.mark.offline
def test_the_vendor_exception_has_not_quietly_grown():
    """The exception exists because prefixing breaks the library that reads the
    name. It is not a place to put variables that were awkward to rename."""
    assert len(VENDOR) <= 40, (
        "the vendor carve-out is growing; each entry must be traceable to a "
        "library that reads that exact name"
    )
