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
    storage_class,
    STORAGE_ENV_REQUIREMENTS,
    STORAGE_IMPLEMENTATIONS,
    verify_storage_implementation,
)

FILE_BASED = ["JsonKVStorage", "JsonDocStatusStorage", "NanoVectorDBStorage", "NetworkXStorage"]
POSTGRES = ["PGKVStorage", "PGVectorStorage", "PGGraphStorage", "PGDocStatusStorage"]
NEO4J = ["Neo4JStorage"]


def _resolve(name: str):
    """The resolver the engine uses — **called, not re-implemented** (AS6).

    This used to be `importlib.import_module(STORAGES[name], package="weave_core")`
    under a docstring claiming it was *"exactly what the engine does at startup"*.
    It was not. It **hardcoded the package the resolver was supposed to supply**,
    so it proved every module path existed while never exercising the lookup —
    and the lookup was broken: `lazy_external_import` read `__package__` off its
    caller's frame, its only caller lived in `weave_core.graph`, and every
    PostgreSQL and Neo4j start died on `No module named 'weave_core.graph.graph'`.

    Nine green tests, a shipped image that could not start, and the docstring
    asserting the very fidelity it lacked. **A test that goes around the thing it
    names is worse than no test**, because the name retires the question.
    """
    return storage_class(name)


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
    # Absolute, not relative (AS6). Relative paths made the registry resolve
    # differently depending on which package asked, which is how a shipped image
    # could not start on PostgreSQL while every test here passed.
    modules = set(STORAGES.values())
    assert modules == {
        "weave_core.graph.storage.files",
        "weave_core.graph.storage.postgres",
        "weave_core.graph.storage.neo4j",
    }
    assert all(not m.startswith(".") for m in modules), (
        "a relative module path is back in STORAGES — it resolves against the "
        "caller's package, so it is correct from one call site and broken from "
        "another"
    )


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


# ── the engine's own path, because that is what actually starts ──────────────


@pytest.mark.offline
@pytest.mark.parametrize("name", FILE_BASED + POSTGRES + NEO4J)
def test_the_engine_resolves_every_backend(name):
    """Through `WeaveEngine._get_storage_class`, the method a real start calls.

    The registry test above can pass while the engine's path is broken — that is
    exactly what happened. Before this, the engine hardcoded the four file-based
    classes and sent the other five through a dynamic import, so **the default
    deployment never touched the code that was broken.** The asymmetry was the
    hiding place; both are asserted now.
    """
    from weave_core.graph.engine import WeaveEngine

    resolved = WeaveEngine._get_storage_class(None, name)
    assert isinstance(resolved, type)
    assert resolved.__name__ == name


@pytest.mark.offline
def test_an_unknown_backend_is_refused_by_name():
    """A typo in `WEAVE_GRAPH_STORAGE` should say so, not raise `KeyError` from
    inside a dictionary the operator has never heard of."""
    with pytest.raises(ValueError, match="unknown storage implementation"):
        storage_class("PostgresStorage")


@pytest.mark.offline
def test_no_module_installs_a_dependency_at_import(request):
    """A11, enforced rather than intended.

    Several bindings ran `pm.install(...)` while loading — including
    `graph/storage/postgres.py`, on the path the deployment bundle uses. Two
    problems, and the crash was the smaller: with no network the install failed
    and took the process down in a restart loop. The larger one is that **a
    process which installs its own dependencies has a dependency set that is not
    its manifest**, which is what A11 exists to prevent, arriving at runtime
    instead of in a diff.
    """
    import pathlib as _p
    import re as _re

    root = _p.Path(__file__).resolve().parent.parent
    offenders = []
    for pkg in ("weave", "weave_core"):
        for path in sorted((root / pkg).glob("**/*.py")):
            if "__pycache__" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _re.search(r"^\s*pm\.install\(", line):
                    offenders.append(f"{path.relative_to(root)}:{i}")

    assert not offenders, (
        "a module installs a package at runtime:\n  " + "\n  ".join(offenders)
        + "\n\n  Declare it in environment.yml (with a D-NN if it is new), or let "
        "the ImportError name it."
    )


@pytest.mark.offline
def test_nothing_builds_a_module_path_out_of_the_registry_by_hand():
    """The reach, asserted (AS6).

    Fixing the resolver broke two callers that had each rebuilt the convention
    themselves — `weave/cli/migrate.py` and `scripts/check_locators.py` both did
    `__import__(f"weave_core{STORAGES[name]}")`, gluing the prefix on by hand.
    They worked only because the registry's paths happened to be relative, and
    they produced `weave_coreweave_core.graph.…` the moment it stopped.

    Three copies of one convention, and the tests caught two of them only because
    the CLI had its own coverage. A fourth would be found by nobody, so the rule
    is structural: **read the registry, never assemble it.**
    """
    import pathlib as _p
    import re as _re

    root = _p.Path(__file__).resolve().parent.parent
    registry = root / "weave_core" / "graph" / "storage" / "__init__.py"
    offenders = []
    for pkg in ("weave", "weave_core", "scripts", "tests"):
        base = root / pkg
        if not base.exists():
            continue
        for path in sorted(base.glob("**/*.py")):
            # The registry resolves its own entries, and this file quotes the
            # pattern it forbids — both would flag themselves.
            if "__pycache__" in path.parts or path in (registry, _p.Path(__file__).resolve()):
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                # An f-string or concatenation wrapping a STORAGES lookup.
                if _re.search(r'["\']\s*\w+.*\{?\s*STORAGES\[', line) or \
                   _re.search(r'f"[^"]*\{STORAGES\[', line):
                    offenders.append(f"{path.relative_to(root)}:{i}")

    assert not offenders, (
        "a module path is assembled from STORAGES rather than read from it:\n  "
        + "\n  ".join(offenders)
        + "\n\n  Use `storage_class(name)` — the registry resolves its own entries."
    )
