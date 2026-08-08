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
