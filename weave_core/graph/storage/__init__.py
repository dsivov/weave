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
STORAGES = {
    "JsonKVStorage": ".graph.storage.files",
    "JsonDocStatusStorage": ".graph.storage.files",
    "NanoVectorDBStorage": ".graph.storage.files",
    "NetworkXStorage": ".graph.storage.files",
    "PGKVStorage": ".graph.storage.postgres",
    "PGVectorStorage": ".graph.storage.postgres",
    "PGGraphStorage": ".graph.storage.postgres",
    "PGDocStatusStorage": ".graph.storage.postgres",
    "Neo4JStorage": ".graph.storage.neo4j",
}


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
