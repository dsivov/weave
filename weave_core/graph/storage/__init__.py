"""The storage registry — exactly three paths, and no fourth (A4, D-007).

``STORAGES`` is a **string-keyed module map**: the engine resolves an
implementation name to a module at runtime with ``importlib``, so a rename that
a type checker would catch anywhere else is invisible here until the process
starts. That is why ``tests/test_storage_registry.py`` imports every entry on
every path — it is the known trap in a mechanical rename (AS6).

Kept:

* **file-based** — ``JsonKVStorage`` · ``JsonDocStatusStorage`` ·
  ``NanoVectorDBStorage`` · ``NetworkXStorage``, all in :mod:`.files`.
  The default, and single-operator only: its writes are whole-file
  read-modify-write (R10).
* **PostgreSQL** — KV, vector, graph and doc-status in one service, and the
  ``LISTEN/NOTIFY`` channel the multi-worker event bus rides on (A7, D-019).
* **Neo4j** — the optional dedicated graph engine.

Dropped with their modules (D-007): Mongo, Milvus, Memgraph, Redis, Qdrant,
Faiss and AGE. Adding a fourth backend makes a sentence in ``CONSTRAINTS.md``
false — it is a contract amendment, not a commit.
"""

STORAGE_IMPLEMENTATIONS = {
    "KV_STORAGE": {
        "implementations": [
            "JsonKVStorage",
            "PGKVStorage",
        ],
        "required_methods": ["get_by_id", "upsert"],
    },
    "GRAPH_STORAGE": {
        "implementations": [
            "NetworkXStorage",
            "Neo4JStorage",
            "PGGraphStorage",
        ],
        "required_methods": ["upsert_node", "upsert_edge"],
    },
    "VECTOR_STORAGE": {
        "implementations": [
            "NanoVectorDBStorage",
            "PGVectorStorage",
        ],
        "required_methods": ["query", "upsert"],
    },
    "DOC_STATUS_STORAGE": {
        "implementations": [
            "JsonDocStatusStorage",
            "PGDocStatusStorage",
        ],
        "required_methods": ["get_docs_by_status"],
    },
}

# Environment a storage implementation needs before it can be selected.
# The file-based path needs nothing, which is what makes first run trivial.
#
# THESE ARE LOOKUPS, NOT LABELS. `check_storage_env_vars()` reads each name out
# of `os.environ` at engine start and refuses to start when one is missing, so a
# name here that configuration does not actually write is not a cosmetic slip:
# it rejects a correctly configured deployment and tells the operator to set a
# variable nothing reads. They must stay in step with `weave/server/config.py`,
# which `tests/test_storage_registry.py` asserts.
STORAGE_ENV_REQUIREMENTS: dict[str, list[str]] = {
    # file-based
    "JsonKVStorage": [],
    "JsonDocStatusStorage": [],
    "NanoVectorDBStorage": [],
    "NetworkXStorage": [],
    # PostgreSQL
    "PGKVStorage": ["WEAVE_POSTGRES_USER", "WEAVE_POSTGRES_PASSWORD", "WEAVE_POSTGRES_DATABASE"],
    "PGVectorStorage": ["WEAVE_POSTGRES_USER", "WEAVE_POSTGRES_PASSWORD", "WEAVE_POSTGRES_DATABASE"],
    "PGGraphStorage": ["WEAVE_POSTGRES_USER", "WEAVE_POSTGRES_PASSWORD", "WEAVE_POSTGRES_DATABASE"],
    "PGDocStatusStorage": ["WEAVE_POSTGRES_USER", "WEAVE_POSTGRES_PASSWORD", "WEAVE_POSTGRES_DATABASE"],
    # Neo4j
    "Neo4JStorage": ["WEAVE_NEO4J_URI", "WEAVE_NEO4J_USERNAME", "WEAVE_NEO4J_PASSWORD"],
}

# Implementation name -> module, relative to ``weave_core``.
# Resolved by ``importlib.import_module(path, package="weave_core")``.
#: **Absolute module paths, deliberately.**
#:
#: These were relative (``.graph.storage.postgres``) and resolved by a helper
#: that read the *caller's* ``__package__`` out of the stack frame. The only
#: caller lives in ``weave_core.graph``, so every lookup doubled into
#: ``weave_core.graph.graph.storage.…`` and every PostgreSQL and Neo4j start
#: died with ``No module named 'weave_core.graph.graph'``.
#:
#: A registry whose entries only resolve correctly depending on **who is asking**
#: is the fragility here, not the rename that exposed it. Absolute paths cannot
#: be wrong from a different caller, and `importlib.import_module` needs no
#: ``package`` argument for them — so there is nothing left to get wrong.
STORAGES = {
    "JsonKVStorage": "weave_core.graph.storage.files",
    "JsonDocStatusStorage": "weave_core.graph.storage.files",
    "NanoVectorDBStorage": "weave_core.graph.storage.files",
    "NetworkXStorage": "weave_core.graph.storage.files",
    "PGKVStorage": "weave_core.graph.storage.postgres",
    "PGVectorStorage": "weave_core.graph.storage.postgres",
    "PGGraphStorage": "weave_core.graph.storage.postgres",
    "PGDocStatusStorage": "weave_core.graph.storage.postgres",
    "Neo4JStorage": "weave_core.graph.storage.neo4j",
}


def storage_class(storage_name: str):
    """Resolve an implementation name to its class — the one lookup (A4).

    Every backend goes through here, including the file-based four. They used to
    be hardcoded `if` branches in the engine while the other five went through a
    dynamic path, which meant **the default deployment never exercised the
    resolver** — so the resolver could be broken for years and only a PostgreSQL
    or Neo4j start would say so. That asymmetry is why this survived the rename
    and every test after it.
    """
    import importlib

    try:
        module = importlib.import_module(STORAGES[storage_name])
    except KeyError:
        raise ValueError(
            f"unknown storage implementation '{storage_name}'; "
            f"known: {', '.join(sorted(STORAGES))}"
        ) from None
    return getattr(module, storage_name)


def verify_storage_implementation(storage_type: str, storage_name: str) -> None:
    """Check an implementation is compatible with the storage role it is asked to fill.

    Args:
        storage_type: one of ``KV_STORAGE`` · ``GRAPH_STORAGE`` · ``VECTOR_STORAGE`` ·
            ``DOC_STATUS_STORAGE``.
        storage_name: the implementation name, as it appears in :data:`STORAGES`.

    Raises:
        ValueError: if the type is unknown, or the implementation cannot fill it.
    """
    if storage_type not in STORAGE_IMPLEMENTATIONS:
        raise ValueError(f"Unknown storage type: {storage_type}")

    storage_info = STORAGE_IMPLEMENTATIONS[storage_type]
    if storage_name not in storage_info["implementations"]:
        raise ValueError(
            f"Storage implementation '{storage_name}' is not compatible with {storage_type}. "
            f"Compatible implementations are: {', '.join(storage_info['implementations'])}"
        )


# ── quadruple mode is not supported on every backend yet (A4 v5, D-039) ──────

#: Vector-store implementations that cannot serve quadruple mode, and the
#: namespaces they are missing.
#:
#: Quadruple mode creates `decisions` and `communities` vector stores
#: unconditionally (`graph/quadruple.py`). `PGVectorStorage` has no tables for
#: either — they are absent from `NAMESPACE_TABLE_MAP`, there is no DDL, and
#: `upsert` dispatches over three namespaces only. So the pair has never worked
#: on any commit, while `deploy/compose.yml` ships exactly that pair as its
#: default.
#:
#: **This map is deleted by P9, not widened.** The tempting way to close a gap
#: like this is to keep adding exceptions until the error stops; the fix is the
#: two tables, and then this goes away.
QUADRUPLE_UNSUPPORTED_VECTOR_STORES = {
    "PGVectorStorage": ("decisions", "communities"),
}


class QuadrupleUnsupported(RuntimeError):
    """Raised at startup, before anything is constructed (D-039)."""


def assert_quadruple_supported(vector_storage: str, quadruple_enabled: bool) -> None:
    """Refuse a combination that cannot start, at the moment it is chosen.

    Not a warning and not a degraded mode. Without this the server reaches
    `ValueError: Unknown namespace: decisions` from forty frames inside the
    engine, on the *first* vector store it happens to build — an error that
    names neither the backend, nor quadruple mode, nor the fact that the pair is
    a known gap.
    """
    if not quadruple_enabled:
        return
    missing = QUADRUPLE_UNSUPPORTED_VECTOR_STORES.get(vector_storage)
    if not missing:
        return

    raise QuadrupleUnsupported(
        f"{vector_storage} cannot run Weave's governance mode yet.\n\n"
        f"  Quadruple mode needs a vector store for each of: "
        f"{', '.join(missing)}.\n"
        f"  {vector_storage} has no table for either, so the server would start "
        f"and then fail\n  on the first governed write.\n\n"
        "  You have not misconfigured anything — `deploy/compose.yml` ships this "
        "pair as its\n  default, and it has never worked. It is a known gap with "
        "a phase behind it (D-039,\n  A4 v5); P9 adds the tables and removes this "
        "refusal.\n\n"
        "  Until then, either:\n"
        "    · set WEAVE_ENABLE_QUADRUPLE=false to run retrieval without "
        "governance, or\n"
        "    · use the file-based vector store (WEAVE_VECTOR_STORAGE="
        "NanoVectorDBStorage) —\n      single-operator only, because its writes "
        "are whole-file read-modify-write (A4)."
    )
