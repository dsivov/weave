"""Pytest configuration for Weave's own test suite.

Kept independent of ``weave_core``'s ``tests/conftest.py`` so Weave tests are
self-contained: integration tests (which load the real similarity model) are
skipped unless ``--run-integration`` is passed.

Markers are registered in ``pyproject.toml``; this file only handles the
``--run-integration`` gate. ``addoption`` is guarded so it coexists with
weave_core's conftest if both are ever collected in one session.
"""

import sys

import pytest

# ── environment preflight ────────────────────────────────────────────────────
# Run this suite outside the declared conda environment and roughly 90 modules
# fail to import, every one of them an identical ModuleNotFoundError buried in a
# collection traceback. At a glance that is indistinguishable from catastrophic
# breakage — the M0 reviewer hit it and came within a hair of filing 90 bogus
# Critical findings against a tree that was green.
#
# So: check the declared third-party set once, and fail with ONE message that
# names the environment instead of ninety that name a symptom. CI is unaffected
# (it builds from environment.yml); this only ever fires for a human.
_REQUIRED = {
    "business_rule_engine": "the rules engine's fuzzy field matching",
    "model2vec": "the rules engine's similarity model",
    "fastapi": "the HTTP surface",
    "networkx": "the file-based graph path",
    "nano_vectordb": "the file-based vector path",
    "jwt": "PyJWT — token issue and verify",
    "bcrypt": "password hashing",
}


def _preflight() -> None:
    import importlib.util

    missing = [
        f"{name} ({why})"
        for name, why in _REQUIRED.items()
        if importlib.util.find_spec(name) is None
    ]
    if not missing:
        return
    raise pytest.UsageError(
        "Weave's declared environment is not active — "
        f"{len(missing)} of {len(_REQUIRED)} required packages are missing:\n"
        + "".join(f"    · {m}\n" for m in missing)
        + f"\n  running under: {sys.executable}\n\n"
        "  environment.yml is the single dependency manifest (D-006, A11):\n"
        "      conda env create -f environment.yml    # first time\n"
        "      conda activate weave\n"
        "      pip install -e . --no-deps\n\n"
        "  Every test failure you would have seen below is this, repeated."
    )


_preflight()

# Some Weave tests exercise the FastAPI rules router, whose auth dependency lazily
# triggers weave.server.config.parse_args(). Make sys.argv look like a server
# invocation so that parsing doesn't choke on pytest's argv (mirrors
# tests/conftest.py).
if not sys.argv or not sys.argv[0].endswith("weave_core-server"):
    sys.argv = ["weave_core-server"]


def pytest_addoption(parser):
    try:
        parser.addoption(
            "--run-integration",
            action="store_true",
            default=False,
            help="Run integration tests (real similarity model, external services).",
        )
    except ValueError:
        # Option already registered by another conftest in a full-suite run.
        pass


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(
        reason="requires the real similarity model — pass --run-integration to run"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
