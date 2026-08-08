"""The string-keyed storage registry resolves on all three paths (AS6, A4).

**This is the known trap in a 92k-line mechanical rename.** ``STORAGES`` maps an
implementation name to a module *as a string*, resolved at runtime with
``importlib``. Every other rename in P0 is caught by the interpreter the moment
something imports it; this one is caught only when a deployment starts with that
backend selected — which, for the two production paths, is the first time a team
tries to use them.

So the registry is tested directly: every declared name must import, every
declared class must exist, and the three kept paths must be the only three.

The Postgres and Neo4j modules import their drivers at module load, so those two
assertions need the drivers installed but not a running database — no server, no
container, no network.
"""

from __future__ import annotations

import importlib

import pytest

from weave_core.graph.storage import (
    STORAGES,
    STORAGE_ENV_REQUIREMENTS,
    STORAGE_IMPLEMENTATIONS,
    verify_storage_implementation,
)

FILE_BASED = ["JsonKVStorage", "JsonDocStatusStorage", "NanoVectorDBStorage", "NetworkXStorage"]
POSTGRES = ["PGKVStorage", "PGVectorStorage", "PGGraphStorage", "PGDocStatusStorage"]
NEO4J = ["Neo4JStorage"]


def _resolve(name: str):
    """Exactly what the engine does at startup: name -> module -> class."""
    module = importlib.import_module(STORAGES[name], package="weave_core")
    return getattr(module, name)


@pytest.mark.offline
@pytest.mark.parametrize("name", FILE_BASED)
def test_file_based_path_resolves(name):
    assert isinstance(_resolve(name), type)


@pytest.mark.offline
@pytest.mark.parametrize("name", POSTGRES)
def test_postgres_path_resolves(name):
    assert isinstance(_resolve(name), type)


@pytest.mark.offline
@pytest.mark.parametrize("name", NEO4J)
def test_neo4j_path_resolves(name):
    assert isinstance(_resolve(name), type)


@pytest.mark.offline
def test_registry_holds_exactly_the_three_supported_paths():
    """A fourth backend makes A4 false — it is an amendment, not a commit."""
    assert set(STORAGES) == set(FILE_BASED) | set(POSTGRES) | set(NEO4J)
    modules = set(STORAGES.values())
    assert modules == {
        ".graph.storage.files",
        ".graph.storage.postgres",
        ".graph.storage.neo4j",
    }


@pytest.mark.offline
def test_every_declared_implementation_is_registered():
    """A name offered in the role table but missing from STORAGES is a
    configuration a user can select and the server cannot start with."""
    for role, info in STORAGE_IMPLEMENTATIONS.items():
        for name in info["implementations"]:
            assert name in STORAGES, f"{role} offers {name}, which STORAGES cannot resolve"
            assert name in STORAGE_ENV_REQUIREMENTS, f"{name} declares no environment needs"


@pytest.mark.offline
def test_every_implementation_fills_its_declared_role():
    for role, info in STORAGE_IMPLEMENTATIONS.items():
        for name in info["implementations"]:
            verify_storage_implementation(role, name)          # must not raise
            cls = _resolve(name)
            for method in info["required_methods"]:
                assert hasattr(cls, method), f"{name} cannot fill {role}: no {method}()"


@pytest.mark.offline
def test_the_file_based_path_needs_no_environment():
    """What makes first run trivial: the default path asks for nothing."""
    for name in FILE_BASED:
        assert STORAGE_ENV_REQUIREMENTS[name] == []


@pytest.mark.offline
def test_an_unsupported_backend_is_refused():
    with pytest.raises(ValueError):
        verify_storage_implementation("KV_STORAGE", "RedisKVStorage")
    with pytest.raises(ValueError):
        verify_storage_implementation("GRAPH_STORAGE", "MemgraphStorage")
    with pytest.raises(ValueError):
        verify_storage_implementation("NOT_A_ROLE", "JsonKVStorage")
