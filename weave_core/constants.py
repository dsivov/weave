"""
Centralized configuration constants for WeaveEngine.

This module defines default values for configuration constants used across
different parts of the WeaveEngine system. Centralizing these values ensures
consistency and makes maintenance easier.
"""

# Default values for server settings
DEFAULT_WOKERS = 2
DEFAULT_MAX_GRAPH_NODES = 1000

#: The two type names the **pipeline** writes when nothing told it a type (W47).
#:
#: Neither is a model's judgement and neither is an ontology type; both mean *a
#: node had to exist because something referred to it*:
#:
#: * ``UNKNOWN`` — `operate.py` creates an endpoint when a *relation* names an
#:   entity the extractor never emitted an entity record for. Without it the
#:   edge would dangle.
#: * ``ENTITY`` — `emit_decision_trace` creates a missing endpoint of a
#:   governance audit edge, for the same reason.
#:
#: They are declared together because they are one condition under two
#: inherited spellings, and because a measurement that lumps them in with a
#: model returning ``concept`` is answering two questions with one number: *what
#: did the model get wrong* and *what did we invent*. The second is ours and is
#: far cheaper to trace.
PLACEHOLDER_ENTITY_TYPES = frozenset({"UNKNOWN", "ENTITY"})

#: The ontology's own declared answer to *"none of these apply"*. It is in the
#: extraction prompt, so a node typed this way is legal rather than off-schema —
#: which is why an invented endpoint is typed `Other` rather than a word the
#: workspace never declared (W47).
OTHER_ENTITY_TYPE = "Other"

#: Marks a node the pipeline **invented** rather than extracted, and why.
#:
#: Typing invented endpoints `Other` makes them ontology-legal and destroys the
#: only thing that told them apart from the model's own `Other` — so the reason
#: is recorded explicitly. Without it a measurement cannot separate *what
#: extraction produced* from *what we had to conjure to keep an edge attached*,
#: which is exactly the denominator error W47 was published with.
INVENTED_MARKER = "created_as"
INVENTED_RELATIONSHIP_ENDPOINT = "relationship_endpoint"
INVENTED_DECISION_ENDPOINT = "decision_endpoint"

# Default values for extraction settings
DEFAULT_SUMMARY_LANGUAGE = "English"  # Default language for document processing
DEFAULT_MAX_GLEANING = 1
DEFAULT_ENTITY_NAME_MAX_LENGTH = 256

# Number of description fragments to trigger LLM summary
DEFAULT_FORCE_LLM_SUMMARY_ON_MERGE = 8
# Max description token size to trigger LLM summary
DEFAULT_SUMMARY_MAX_TOKENS = 1200
# Recommended LLM summary output length in tokens
DEFAULT_SUMMARY_LENGTH_RECOMMENDED = 600
# Maximum token size sent to LLM for summary
DEFAULT_SUMMARY_CONTEXT_SIZE = 12000
# Maximum token size allowed for entity extraction input context
DEFAULT_MAX_EXTRACT_INPUT_TOKENS = 20480
# The entity types a **library** caller extracts with when nothing else says.
#
# **Not the product's default any more** (P15, D-050). The Weave server resolves
# types per extraction run: explicit `WEAVE_ENTITY_TYPES` → the workspace's
# installed ontology → the shipped preset. This list shares no type with that
# ontology, so anything extracted under it is invisible to the four standing
# questions; `LossReason`, `Objection` and `Competitor` are the parent engine's
# sales vocabulary, the same carry-over as D-041 one layer down.
DEFAULT_ENTITY_TYPES = [
    "Person",
    "Creature",
    "Organization",
    "Location",
    "Event",
    "Concept",
    "Method",
    "Content",
    "Data",
    "Artifact",
    "NaturalObject",
    "LossReason",
    "Objection",
    "Competitor",
]

# Separator for: description, source_id and relation-key fields(Can not be changed after data inserted)
GRAPH_FIELD_SEP = "<SEP>"

# Query and retrieval configuration defaults
DEFAULT_TOP_K = 40
DEFAULT_CHUNK_TOP_K = 20
DEFAULT_MAX_ENTITY_TOKENS = 6000
DEFAULT_MAX_RELATION_TOKENS = 8000
DEFAULT_MAX_TOTAL_TOKENS = 30000
DEFAULT_COSINE_THRESHOLD = 0.2
DEFAULT_RELATED_CHUNK_NUMBER = 5
DEFAULT_KG_CHUNK_PICK_METHOD = "VECTOR"

# TODO: Deprated. All conversation_history messages is send to LLM.
DEFAULT_HISTORY_TURNS = 0

# Rerank configuration defaults
DEFAULT_MIN_RERANK_SCORE = 0.0
DEFAULT_RERANK_BINDING = "null"

# Default source ids limit in meta data for entity and relation
DEFAULT_MAX_SOURCE_IDS_PER_ENTITY = 300
DEFAULT_MAX_SOURCE_IDS_PER_RELATION = 300
### control chunk_ids limitation method: FIFO, FIFO
###    FIFO: First in first out
###    KEEP: Keep oldest (less merge action and faster)
SOURCE_IDS_LIMIT_METHOD_KEEP = "KEEP"
SOURCE_IDS_LIMIT_METHOD_FIFO = "FIFO"
DEFAULT_SOURCE_IDS_LIMIT_METHOD = SOURCE_IDS_LIMIT_METHOD_FIFO
VALID_SOURCE_IDS_LIMIT_METHODS = {
    SOURCE_IDS_LIMIT_METHOD_KEEP,
    SOURCE_IDS_LIMIT_METHOD_FIFO,
}
# Maximum number of file paths stored in entity/relation file_path field (For displayed only, does not affect query performance)
DEFAULT_MAX_FILE_PATHS = 100

# Field length of file_path in Milvus Schema for entity and relation (Should not be changed)
# file_path must store all file paths up to the DEFAULT_MAX_FILE_PATHS limit within the metadata.
DEFAULT_MAX_FILE_PATH_LENGTH = 32768
# Placeholder for more file paths in meta data for entity and relation (Should not be changed)
DEFAULT_FILE_PATH_MORE_PLACEHOLDER = "truncated"

# Default temperature for LLM
DEFAULT_TEMPERATURE = 1.0

# Async configuration defaults
DEFAULT_MAX_ASYNC = 4  # Default maximum async operations
DEFAULT_MAX_PARALLEL_INSERT = 2  # Default maximum parallel insert operations

# Embedding configuration defaults
DEFAULT_EMBEDDING_FUNC_MAX_ASYNC = 8  # Default max async for embedding functions
DEFAULT_EMBEDDING_BATCH_NUM = 10  # Default batch size for embedding computations

# Gunicorn worker timeout
DEFAULT_TIMEOUT = 300

# Default llm and embedding timeout
DEFAULT_LLM_TIMEOUT = 180
DEFAULT_EMBEDDING_TIMEOUT = 30

# Logging configuration defaults
DEFAULT_LOG_MAX_BYTES = 10485760  # Default 10MB
DEFAULT_LOG_BACKUP_COUNT = 5  # Default 5 backups
DEFAULT_LOG_FILENAME = "weave_core.log"  # Default log filename

# Ollama server configuration defaults
DEFAULT_OLLAMA_MODEL_NAME = "weave_core"
DEFAULT_OLLAMA_MODEL_TAG = "latest"
DEFAULT_OLLAMA_MODEL_SIZE = 7365960935
DEFAULT_OLLAMA_CREATED_AT = "2024-01-15T00:00:00Z"
DEFAULT_OLLAMA_DIGEST = "sha256:weave_core"
