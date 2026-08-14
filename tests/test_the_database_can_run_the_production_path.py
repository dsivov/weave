"""The bundle's database provides both extensions the production path needs (P12, D-046).

`PGGraphStorage` requires **`age` and `vector` in one database**, and no
published image provides both:

    pgvector/pgvector:pg16      vector ✓  age ✗   → dies at `create_graph(unknown)`
    apache/age:release_PG16_…   age ✓     vector ✗ → dies on connect, because
                                                     `PostgreSQLDB` creates the
                                                     vector extension whichever
                                                     storage is in use

So AGE-alone is not a partial configuration, it is no configuration — one image
with both, or nothing. `deploy/postgres.Dockerfile` builds that image.

**What this file can and cannot establish.** It is structural: it reads the
Dockerfile and the compose file. It cannot build an image, start a container, or
execute a query — this container has no Docker.

That limit is the reason the file says so out loud. **`pgvector/pgvector:pg16`
builds, starts, and passes every test in this suite while being unable to run the
adapter**, which is exactly how the defect survived seven milestone gates. A
structural test that read as proof would be the same mistake in a new place.

The gate is `tests/test_storage_paths.py::test_the_postgres_graph_path` — a graph
round-trip on a live server — and until it stops skipping, nothing here should be
read as the production path working.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "deploy" / "postgres.Dockerfile"
_COMPOSE = _REPO / "deploy" / "compose.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    """The Dockerfile's **instructions**, with comments stripped.

    The fourth time in two phases that a matcher of mine has flagged the comment
    *explaining* a fix as though it were the fix's absence — here, a comment
    naming `/usr/lib/postgresql/16/lib/vector.so` as the path we deliberately do
    not hard-code. Where the language has an AST I parse it; a Dockerfile has no
    parser here, and dropping `#` lines is the whole of it.

    The general form is worth keeping in mind when writing any sweep: **prose
    that names the defect lives in the same file as the code that avoids it**,
    and a guard which cannot tell them apart will either fire on the
    documentation or be narrowed until it fires on nothing.
    """
    return "\n".join(
        line for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


# ── the image ────────────────────────────────────────────────────────────────


def test_the_image_starts_from_a_base_that_carries_age(dockerfile):
    """AGE is the base because it is the expensive half to compile — pgvector is
    one `make install`, while building AGE needs `postgresql-server-dev-16`,
    flex and bison."""
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, re.M)
    assert bases, "the Dockerfile declares no base image"
    assert all("apache/age" in base for base in bases), (
        f"the image no longer builds on an AGE base: {bases}"
    )


def test_every_base_is_pinned(dockerfile):
    """`apache/age:latest` is PostgreSQL 13. A floating tag here would move the
    server major underneath an extension compiled against a specific ABI, and
    the failure would arrive as a `.so` that will not load."""
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, re.M)
    for base in bases:
        assert ":" in base, f"unpinned base image: {base}"
        assert not base.endswith(":latest"), f"floating tag: {base}"


def test_the_two_stages_share_one_base(dockerfile):
    """An extension is compiled against a specific PostgreSQL major and ABI.
    Building it on one base and installing it on another produces a `.so` that
    loads on a good day."""
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, re.M)
    assert len(set(bases)) == 1, (
        f"the build stage and the final image use different bases: {set(bases)}"
    )


def test_pgvector_is_pinned_to_a_version(dockerfile):
    """And to the one already in use, so this change adds a capability rather
    than also moving an existing one."""
    match = re.search(r"ARG PGVECTOR_VERSION=(\S+)", dockerfile)
    assert match, "the pgvector version is not pinned"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", match.group(1)), (
        f"pgvector version is not an exact tag: {match.group(1)}"
    )


def test_the_extension_is_installed_by_prefix_not_by_hard_coded_path(dockerfile):
    """The install prefix comes from `pg_config`. A literal
    `/usr/lib/postgresql/16/lib/vector.so` is a silent miss the day the base
    image moves it: the file is simply absent and `CREATE EXTENSION vector`
    fails at runtime with nothing to point at."""
    assert "DESTDIR=/staging" in dockerfile
    assert "COPY --from=pgvector /staging/ /" in dockerfile
    assert "/usr/lib/postgresql/16/lib/vector.so" not in dockerfile, (
        "the extension is copied from a hard-coded path"
    )


def test_age_is_preloaded_because_the_adapter_never_loads_it(dockerfile):
    """**Read from the adapter, not assumed.**

    `weave_core/graph/storage/postgres.py` issues `CREATE EXTENSION AGE CASCADE`
    and `SET search_path = ag_catalog, …` and **no `LOAD 'age'`** — so the
    library has to already be in the server. Putting it in
    `postgresql.conf.sample`, which the official entrypoint's `initdb` reads,
    makes that true however the container is started rather than only under our
    compose file.
    """
    assert "shared_preload_libraries = 'age'" in dockerfile
    assert "postgresql.conf.sample" in dockerfile

    adapter = (_REPO / "weave_core" / "graph" / "storage" / "postgres.py").read_text(
        encoding="utf-8")
    assert "LOAD 'age'" not in adapter, (
        "the adapter now loads AGE itself, so the preload in the image is no "
        "longer the reason this works — say which one is load-bearing"
    )


# ── the bundle uses it ───────────────────────────────────────────────────────


def test_the_bundle_no_longer_runs_the_image_that_cannot_work(compose):
    """The specific regression: `pgvector/pgvector:pg16` was the default database
    while `PGGraphStorage` was the default graph store."""
    postgres = compose["services"]["postgres"]
    assert "pgvector/pgvector" not in str(postgres.get("image", "")), (
        "the bundle is back on an image with no `age` — its default graph store "
        "cannot run there"
    )
    assert postgres["build"]["dockerfile"] == "deploy/postgres.Dockerfile"


def test_the_default_graph_store_is_the_one_the_database_supports(compose):
    """The two halves of the same decision, in two files. When they disagreed,
    the bundle crashed at first use with an error about an unknown function."""
    environment = compose["services"]["server"]["environment"]
    assert "PGGraphStorage" in environment["WEAVE_GRAPH_STORAGE"]


def test_the_graph_store_is_still_overridable(compose):
    """W20: the refusal advice tells an operator to set these, and as literals
    those exports did nothing — the only way out was editing the compose file. A
    refusal you can act on beats one you have to edit around."""
    environment = compose["services"]["server"]["environment"]
    for variable in ("WEAVE_GRAPH_STORAGE", "WEAVE_VECTOR_STORAGE",
                     "WEAVE_KV_STORAGE", "WEAVE_ENABLE_QUADRUPLE"):
        value = str(environment.get(variable, ""))
        assert value.startswith("${") and ":-" in value, (
            f"{variable} is a literal in compose.yml, so overriding it does "
            "nothing and the advice that names it is a dead end"
        )


# ── and this file is not the gate ────────────────────────────────────────────


def test_the_round_trip_is_still_the_gate():
    """**Said in a test, because it is the thing most likely to be forgotten.**

    Everything above reads files. The previous image passed every file-reading
    test in this repository and could not run the adapter. If
    `test_the_postgres_graph_path` is ever deleted or defanged, this suite would
    go on reporting a working production path built entirely out of string
    matching.
    """
    source = (_REPO / "tests" / "test_storage_paths.py").read_text(encoding="utf-8")
    assert "async def test_the_postgres_graph_path" in source
    assert "_exercise_a_graph_round_trip(store)" in source, (
        "the PostgreSQL graph test no longer performs the round-trip"
    )
    assert "PGGraphStorage(" in source, (
        "the PostgreSQL graph test no longer constructs the adapter — an "
        "importability check reading as coverage is the defect this phase exists "
        "to close"
    )
