"""
Configs for the WeaveEngine API.
"""

import os
import argparse
import logging
from dotenv import load_dotenv
from weave.server import DEFAULT_WORKERS, DEFAULT_WORKING_DIR
from weave_core.utils import get_env_value
from weave_core.llm.binding_options import (
    GeminiEmbeddingOptions,
    GeminiLLMOptions,
    OllamaEmbeddingOptions,
    OllamaLLMOptions,
    OpenAILLMOptions,
)
import sys

from weave_core.constants import (
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_K,
    DEFAULT_CHUNK_TOP_K,
    DEFAULT_HISTORY_TURNS,
    DEFAULT_MAX_ENTITY_TOKENS,
    DEFAULT_MAX_RELATION_TOKENS,
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_COSINE_THRESHOLD,
    DEFAULT_RELATED_CHUNK_NUMBER,
    DEFAULT_MIN_RERANK_SCORE,
    DEFAULT_FORCE_LLM_SUMMARY_ON_MERGE,
    DEFAULT_MAX_ASYNC,
    DEFAULT_SUMMARY_MAX_TOKENS,
    DEFAULT_SUMMARY_LENGTH_RECOMMENDED,
    DEFAULT_SUMMARY_CONTEXT_SIZE,
    DEFAULT_SUMMARY_LANGUAGE,
    DEFAULT_EMBEDDING_FUNC_MAX_ASYNC,
    DEFAULT_EMBEDDING_BATCH_NUM,
    DEFAULT_RERANK_BINDING,
)

# use the .env that is inside the current folder
# allows to use different .env file for each weave_core instance
# the OS environment variables take precedence over the .env file
load_dotenv(dotenv_path=".env", override=False)




class DefaultRAGStorageConfig:
    KV_STORAGE = "JsonKVStorage"
    VECTOR_STORAGE = "NanoVectorDBStorage"
    GRAPH_STORAGE = "NetworkXStorage"
    DOC_STATUS_STORAGE = "JsonDocStatusStorage"


#: The event-bus adapters, and which deployment shape each is correct for (A7).
#:
#: `inprocess` is the default because the default storage path is file-based,
#: which is single-operator and therefore single-worker (A4). The pairing is
#: self-consistent: the default deployment is exactly the one the in-process bus
#: is correct for.
IN_PROCESS_BUS = "inprocess"
POSTGRES_BUS = "postgres"
EVENT_BUSES = (IN_PROCESS_BUS, POSTGRES_BUS)
DEFAULT_EVENT_BUS = IN_PROCESS_BUS


class BusDeploymentMismatch(RuntimeError):
    """The event-bus adapter does not match the deployment shape (A7, D-019).

    A startup error rather than a warning, and deliberately so. The failure it
    replaces is *silent*: on the in-process bus behind more than one worker, a
    client connected to worker 2 never receives an event published on worker 1 —
    nothing raises, nothing logs, the board simply stops updating for some users
    and not others. A warning at boot would scroll past; a refusal cannot.
    """


def create_event_bus(event_bus: str):
    """The one place an event bus is constructed (A7).

    Every publisher and subscriber in the server must be on the *same* adapter,
    and it must be the one the deployment selected. A module that builds its own
    `InProcessBus()` is not merely redundant — under several workers it silently
    opts that whole subsystem out of fan-out, while `assert_bus_matches_deployment`
    goes on passing because it only ever sees the configured name. The ingress
    service did exactly that before this factory existed.

    That is watch item W4 in its structural form: a rule enforced at one
    construction site protects only the callers who construct there. The fix is
    to leave one site.
    """
    if event_bus == POSTGRES_BUS:
        from weave_core.events.postgres import PostgresEventBus

        return PostgresEventBus()

    from weave_core.events import InProcessBus

    return InProcessBus()


def assert_bus_matches_deployment(event_bus: str, workers: int) -> None:
    """Refuse a multi-worker deployment on the in-process bus (A7).

    This is the other half of the PostgreSQL adapter, and it ships with it
    rather than after it: an adapter that removes a silent failure, plus a
    configuration that still permits the failure, is not a fix.

    Note which way the check runs. It refuses *in-process + many workers*. It
    does not refuse PostgreSQL with one worker — that pairing is merely
    unnecessary, not wrong, and refusing it would break the ordinary case of
    running a single worker against a production database.
    """
    if event_bus not in EVENT_BUSES:
        raise BusDeploymentMismatch(
            f"Unknown event bus '{event_bus}'. Set WEAVE_EVENT_BUS to one of: "
            + ", ".join(EVENT_BUSES)
        )
    if event_bus == IN_PROCESS_BUS and workers > 1:
        raise BusDeploymentMismatch(
            f"Refusing to start: {workers} workers on the in-process event bus.\n\n"
            "  The in-process bus cannot fan out across worker processes. A client\n"
            "  connected to one worker would never receive an event published on\n"
            "  another — with no error and no log. The board would simply stop\n"
            "  updating, for some users and not others.\n\n"
            "  Either use the PostgreSQL bus, which every worker already shares:\n"
            "      export WEAVE_EVENT_BUS=postgres\n"
            "  or run a single worker:\n"
            "      --workers 1\n\n"
            "  (A7, D-019 — the adapter must match the deployment.)"
        )


def get_default_host(binding_type: str) -> str:
    default_hosts = {
        "ollama": os.getenv("WEAVE_LLM_BINDING_HOST", "http://localhost:11434"),
        "lollms": os.getenv("WEAVE_LLM_BINDING_HOST", "http://localhost:9600"),
        "azure_openai": os.getenv("AZURE_OPENAI_ENDPOINT", "https://api.openai.com/v1"),
        "openai": os.getenv("WEAVE_LLM_BINDING_HOST", "https://api.openai.com/v1"),
        "gemini": os.getenv(
            "WEAVE_LLM_BINDING_HOST", "https://generativelanguage.googleapis.com"
        ),
    }
    return default_hosts.get(
        binding_type, os.getenv("WEAVE_LLM_BINDING_HOST", "http://localhost:11434")
    )  # fallback to ollama if unknown


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments with environment variable fallback

    Args:
        is_uvicorn_mode: Whether running under uvicorn mode

    Returns:
        argparse.Namespace: Parsed arguments
    """

    parser = argparse.ArgumentParser(description="WeaveEngine API Server")

    # Server configuration
    parser.add_argument(
        "--host",
        default=get_env_value("WEAVE_HOST", "0.0.0.0"),
        help="Server host (default: from env or 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=get_env_value("WEAVE_PORT", 9621, int),
        help="Server port (default: from env or 9621)",
    )

    # Directory configuration
    parser.add_argument(
        "--working-dir",
        # **The same default the CLI uses** (W27). This said `./rag_storage`
        # while every `weave` command said `./weave_storage`, so creating the
        # first administrator and then starting the server without
        # `WEAVE_WORKING_DIR` produced an account the server could not see —
        # with both halves reporting success.
        default=get_env_value("WEAVE_WORKING_DIR", DEFAULT_WORKING_DIR),
        help=f"Where Weave keeps its state (default: from env or {DEFAULT_WORKING_DIR})",
    )
    parser.add_argument(
        "--input-dir",
        default=get_env_value("WEAVE_INPUT_DIR", "./inputs"),
        help="Directory containing input documents (default: from env or ./inputs)",
    )

    parser.add_argument(
        "--timeout",
        default=get_env_value("WEAVE_TIMEOUT", DEFAULT_TIMEOUT, int, special_none=True),
        type=int,
        help="Timeout in seconds (useful when using slow AI). Use None for infinite timeout",
    )

    # RAG configuration
    parser.add_argument(
        "--max-async",
        type=int,
        default=get_env_value("WEAVE_MAX_ASYNC", DEFAULT_MAX_ASYNC, int),
        help=f"Maximum async operations (default: from env or {DEFAULT_MAX_ASYNC})",
    )
    parser.add_argument(
        "--summary-max-tokens",
        type=int,
        default=get_env_value("WEAVE_SUMMARY_MAX_TOKENS", DEFAULT_SUMMARY_MAX_TOKENS, int),
        help=f"Maximum token size for entity/relation summary(default: from env or {DEFAULT_SUMMARY_MAX_TOKENS})",
    )
    parser.add_argument(
        "--summary-context-size",
        type=int,
        default=get_env_value(
            "WEAVE_SUMMARY_CONTEXT_SIZE", DEFAULT_SUMMARY_CONTEXT_SIZE, int
        ),
        help=f"LLM Summary Context size (default: from env or {DEFAULT_SUMMARY_CONTEXT_SIZE})",
    )
    parser.add_argument(
        "--summary-length-recommended",
        type=int,
        default=get_env_value(
            "WEAVE_SUMMARY_LENGTH_RECOMMENDED", DEFAULT_SUMMARY_LENGTH_RECOMMENDED, int
        ),
        help=f"LLM Summary Context size (default: from env or {DEFAULT_SUMMARY_LENGTH_RECOMMENDED})",
    )

    # Logging configuration
    parser.add_argument(
        "--log-level",
        default=get_env_value("WEAVE_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: from env or INFO)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=get_env_value("WEAVE_VERBOSE", False, bool),
        help="Enable verbose debug output(only valid for DEBUG log-level)",
    )

    parser.add_argument(
        "--key",
        type=str,
        default=get_env_value("WEAVE_API_KEY", None),
        help="API key for authentication. This protects weave_core server against unauthorized access",
    )

    # Optional https parameters
    parser.add_argument(
        "--ssl",
        action="store_true",
        default=get_env_value("WEAVE_SSL", False, bool),
        help="Enable HTTPS (default: from env or False)",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=get_env_value("WEAVE_SSL_CERTFILE", None),
        help="Path to SSL certificate file (required if --ssl is enabled)",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=get_env_value("WEAVE_SSL_KEYFILE", None),
        help="Path to SSL private key file (required if --ssl is enabled)",
    )

    # Namespace
    parser.add_argument(
        "--workspace",
        type=str,
        default=get_env_value("WEAVE_WORKSPACE", ""),
        help="Default workspace for all storage",
    )

    # Server workers configuration
    parser.add_argument(
        "--entity-types",
        type=str,
        default="",
        help=("Comma-separated entity types the extractor looks for. Leave unset "
              "to use the workspace's installed ontology, falling back to the "
              "shipped preset — setting this OVERRIDES both, for every workspace. "
              "Extending the vocabulary for a domain is legitimate; the types "
              "must still be ones the answer surface queries, or the nodes "
              "produced will not be reachable (P15, D-050)."),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=get_env_value("WEAVE_WORKERS", DEFAULT_WORKERS, int),
        help=f"Number of worker processes (default: from env or {DEFAULT_WORKERS})",
    )

    # LLM and embedding bindings
    parser.add_argument(
        "--llm-binding",
        type=str,
        default=get_env_value("WEAVE_LLM_BINDING", "ollama"),
        choices=[
            "lollms",
            "ollama",
            "openai",
            "openai-ollama",
            "azure_openai",
            "aws_bedrock",
            "gemini",
        ],
        help="LLM binding type (default: from env or ollama)",
    )
    parser.add_argument(
        "--embedding-binding",
        type=str,
        default=get_env_value("WEAVE_EMBEDDING_BINDING", "ollama"),
        choices=[
            "lollms",
            "ollama",
            "openai",
            "azure_openai",
            "aws_bedrock",
            "jina",
            "gemini",
        ],
        help="Embedding binding type (default: from env or ollama)",
    )
    parser.add_argument(
        "--rerank-binding",
        type=str,
        default=get_env_value("WEAVE_RERANK_BINDING", DEFAULT_RERANK_BINDING),
        choices=["null", "cohere", "jina", "aliyun"],
        help=f"Rerank binding type (default: from env or {DEFAULT_RERANK_BINDING})",
    )

    # Document loading engine configuration
    parser.add_argument(
        "--docling",
        action="store_true",
        default=False,
        help="Enable DOCLING document loading engine (default: from env or DEFAULT)",
    )

    # Conditionally add binding-specific options (Ollama, OpenAI, Azure OpenAI, Gemini)
    # This registers command line arguments (e.g., --openai-llm-temperature)
    # and reads corresponding environment variables (e.g., OPENAI_LLM_TEMPERATURE)

    # Determine LLM binding value consistently from command line or environment
    llm_binding_value = None
    if "--llm-binding" in sys.argv:
        try:
            idx = sys.argv.index("--llm-binding")
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
                llm_binding_value = sys.argv[idx + 1]
        except IndexError:
            pass

    # Fall back to environment variable using same function as argparse default
    if llm_binding_value is None:
        llm_binding_value = get_env_value("WEAVE_LLM_BINDING", "ollama")

    # Add LLM binding options based on determined value
    if llm_binding_value == "ollama":
        OllamaLLMOptions.add_args(parser)
    elif llm_binding_value in ["openai", "azure_openai"]:
        OpenAILLMOptions.add_args(parser)
    elif llm_binding_value == "gemini":
        GeminiLLMOptions.add_args(parser)

    # Determine embedding binding value consistently from command line or environment
    embedding_binding_value = None
    if "--embedding-binding" in sys.argv:
        try:
            idx = sys.argv.index("--embedding-binding")
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
                embedding_binding_value = sys.argv[idx + 1]
        except IndexError:
            pass

    # Fall back to environment variable using same function as argparse default
    if embedding_binding_value is None:
        embedding_binding_value = get_env_value("WEAVE_EMBEDDING_BINDING", "ollama")

    # Add embedding binding options based on determined value
    if embedding_binding_value == "ollama":
        OllamaEmbeddingOptions.add_args(parser)
    elif embedding_binding_value == "gemini":
        GeminiEmbeddingOptions.add_args(parser)

    args = parser.parse_args()

    # convert relative path to absolute path
    args.working_dir = os.path.abspath(args.working_dir)
    args.input_dir = os.path.abspath(args.input_dir)

    # Inject storage configuration from environment variables
    args.kv_storage = get_env_value(
        "WEAVE_KV_STORAGE", DefaultRAGStorageConfig.KV_STORAGE
    )
    args.doc_status_storage = get_env_value(
        "WEAVE_DOC_STATUS_STORAGE", DefaultRAGStorageConfig.DOC_STATUS_STORAGE
    )
    args.graph_storage = get_env_value(
        "WEAVE_GRAPH_STORAGE", DefaultRAGStorageConfig.GRAPH_STORAGE
    )
    args.vector_storage = get_env_value(
        "WEAVE_VECTOR_STORAGE", DefaultRAGStorageConfig.VECTOR_STORAGE
    )

    # The event-bus adapter is chosen alongside the storage path, not separately,
    # because A7 pairs it with the deployment shape. See assert_bus_matches_deployment.
    args.event_bus = get_env_value("WEAVE_EVENT_BUS", DEFAULT_EVENT_BUS)

    # Get WEAVE_MAX_PARALLEL_INSERT from environment
    args.max_parallel_insert = get_env_value("WEAVE_MAX_PARALLEL_INSERT", 2, int)

    # Get WEAVE_MAX_GRAPH_NODES from environment
    args.max_graph_nodes = get_env_value("WEAVE_MAX_GRAPH_NODES", 1000, int)

    # Handle openai-ollama special case
    if args.llm_binding == "openai-ollama":
        args.llm_binding = "openai"
        args.embedding_binding = "ollama"

    # Ollama ctx_num
    args.ollama_num_ctx = get_env_value("WEAVE_OLLAMA_NUM_CTX", 32768, int)

    args.llm_binding_host = get_env_value(
        "WEAVE_LLM_BINDING_HOST", get_default_host(args.llm_binding)
    )
    args.embedding_binding_host = get_env_value(
        "WEAVE_EMBEDDING_BINDING_HOST", get_default_host(args.embedding_binding)
    )
    args.llm_binding_api_key = get_env_value("WEAVE_LLM_BINDING_API_KEY", None)
    args.embedding_binding_api_key = get_env_value("WEAVE_EMBEDDING_BINDING_API_KEY", "")

    # Inject model configuration
    args.llm_model = get_env_value("WEAVE_LLM_MODEL", "mistral-nemo:latest")

    # Per-task LLM roles (upstream 1.5.x alignment). Each role falls back to the
    # global LLM_* settings when its own vars are unset — so this is fully optional
    # and backward-compatible. Used by WeaveGraph: a cheap/fast model for
    # extraction & verification, a stronger model for CGR3 & synthesis.
    def _role_llm(prefix):
        return {
            "binding": get_env_value(f"{prefix}_LLM_BINDING", args.llm_binding),
            "model": get_env_value(f"{prefix}_LLM_MODEL", args.llm_model),
            "host": get_env_value(f"{prefix}_LLM_BINDING_HOST", args.llm_binding_host),
            "api_key": get_env_value(f"{prefix}_LLM_BINDING_API_KEY", args.llm_binding_api_key),
        }

    args.llm_role_extract = _role_llm("EXTRACT")
    args.llm_role_query = _role_llm("QUERY")
    # WEAVE_EMBEDDING_MODEL defaults to None - each binding will use its own default model
    # e.g., OpenAI uses "text-embedding-3-small", Jina uses "jina-embeddings-v4"
    args.embedding_model = get_env_value("WEAVE_EMBEDDING_MODEL", None, special_none=True)
    # WEAVE_EMBEDDING_DIM defaults to None - each binding will use its own default dimension
    # Value is inherited from provider defaults via wrap_embedding_func_with_attrs decorator
    args.embedding_dim = get_env_value("WEAVE_EMBEDDING_DIM", None, int, special_none=True)
    args.embedding_send_dim = get_env_value("WEAVE_EMBEDDING_SEND_DIM", False, bool)

    # Inject chunk configuration
    args.chunk_size = get_env_value("WEAVE_CHUNK_SIZE", 1200, int)
    args.chunk_overlap_size = get_env_value("WEAVE_CHUNK_OVERLAP_SIZE", 100, int)

    # Inject LLM cache configuration
    args.enable_llm_cache_for_extract = get_env_value(
        "WEAVE_ENABLE_LLM_CACHE_FOR_EXTRACT", True, bool
    )
    args.enable_llm_cache = get_env_value("WEAVE_ENABLE_LLM_CACHE", True, bool)

    # Set document_loading_engine from --docling flag
    if args.docling:
        args.document_loading_engine = "DOCLING"
    else:
        args.document_loading_engine = get_env_value(
            "WEAVE_DOCUMENT_LOADING_ENGINE", "DEFAULT"
        )

    # PDF decryption password
    args.pdf_decrypt_password = get_env_value("WEAVE_PDF_DECRYPT_PASSWORD", None)

    # Add environment variables that were previously read directly
    args.cors_origins = get_env_value("WEAVE_CORS_ORIGINS", "*")
    args.summary_language = get_env_value("WEAVE_SUMMARY_LANGUAGE", DEFAULT_SUMMARY_LANGUAGE)
    # **Empty means "no explicit override"**, not "no types" (P15, D-050).
    #
    # This defaulted to `DEFAULT_ENTITY_TYPES` — the parent engine's fourteen,
    # which share *nothing* with Weave's ontology, so everything the pipeline
    # extracted was typed in a vocabulary the answer surface never looks for.
    # The list now comes from the workspace's installed ontology at extraction
    # time, with the shipped preset as the floor; `WEAVE_ENTITY_TYPES` still
    # wins when an operator sets it, because an override the ontology could
    # outvote is not an override.
    # **The flag has to win, or it is documentation for something that does
    # nothing.** This line unconditionally overwrote whatever `--entity-types`
    # parsed, so the option appeared in `--help` and was discarded — the same
    # shape as W20's refusal advice naming variables that were literals in the
    # compose file. Caught by running the flag rather than by reading it.
    # **One parser for one variable.** `get_env_value(..., list)` wants JSON, so
    # `WEAVE_ENTITY_TYPES=PRD,RFC` failed with a warning and fell back to the
    # default — while the resolver at the head of the chain read the same
    # variable as a comma-separated list. Two readings of one setting, which is
    # this phase's own theme in miniature; D-050's rollback instruction ("set
    # WEAVE_ENTITY_TYPES to the old list") would have been a trap.
    from weave.model.entity_types import explicit_entity_types

    args.entity_types = (
        [part.strip() for part in str(args.entity_types).split(",") if part.strip()]
        if getattr(args, "entity_types", "")
        else explicit_entity_types()
    )
    args.whitelist_paths = get_env_value("WEAVE_WHITELIST_PATHS", "/health,/api/*")

    # For JWT Auth.
    #
    # Accounts are NOT read here. They are persisted records in the user store
    # (A14, D-009), and the only thing that still looks at the old environment
    # variables is weave/server/migrate_accounts.py, which moves them into the
    # store once and never again. Reading them here as well would recreate the
    # exact failure R16 exists to prevent: two sources of truth for a password,
    # disagreeing the moment somebody changes one in the Admin UI.
    args.token_secret = get_env_value("WEAVE_TOKEN_SECRET", "weave_core-jwt-default-secret")
    args.token_expire_hours = get_env_value("WEAVE_TOKEN_EXPIRE_HOURS", 48, float)
    args.guest_token_expire_hours = get_env_value("WEAVE_GUEST_TOKEN_EXPIRE_HOURS", 24, float)
    args.jwt_algorithm = get_env_value("WEAVE_JWT_ALGORITHM", "HS256")

    # Token auto-renewal configuration (sliding window expiration)
    args.token_auto_renew = get_env_value("WEAVE_TOKEN_AUTO_RENEW", True, bool)
    args.token_renew_threshold = get_env_value("WEAVE_TOKEN_RENEW_THRESHOLD", 0.5, float)

    # Rerank model configuration
    args.rerank_model = get_env_value("WEAVE_RERANK_MODEL", None)
    args.rerank_binding_host = get_env_value("WEAVE_RERANK_BINDING_HOST", None)
    args.rerank_binding_api_key = get_env_value("WEAVE_RERANK_BINDING_API_KEY", None)
    # Note: rerank_binding is already set by argparse, no need to override from env

    # Min rerank score configuration
    args.min_rerank_score = get_env_value(
        "WEAVE_MIN_RERANK_SCORE", DEFAULT_MIN_RERANK_SCORE, float
    )

    # Query configuration
    args.history_turns = get_env_value("WEAVE_HISTORY_TURNS", DEFAULT_HISTORY_TURNS, int)
    args.top_k = get_env_value("WEAVE_TOP_K", DEFAULT_TOP_K, int)
    args.chunk_top_k = get_env_value("WEAVE_CHUNK_TOP_K", DEFAULT_CHUNK_TOP_K, int)
    args.max_entity_tokens = get_env_value(
        "WEAVE_MAX_ENTITY_TOKENS", DEFAULT_MAX_ENTITY_TOKENS, int
    )
    args.max_relation_tokens = get_env_value(
        "WEAVE_MAX_RELATION_TOKENS", DEFAULT_MAX_RELATION_TOKENS, int
    )
    args.max_total_tokens = get_env_value(
        "WEAVE_MAX_TOTAL_TOKENS", DEFAULT_MAX_TOTAL_TOKENS, int
    )
    args.cosine_threshold = get_env_value(
        "WEAVE_COSINE_THRESHOLD", DEFAULT_COSINE_THRESHOLD, float
    )
    args.related_chunk_number = get_env_value(
        "WEAVE_RELATED_CHUNK_NUMBER", DEFAULT_RELATED_CHUNK_NUMBER, int
    )

    # Add missing environment variables for health endpoint
    args.force_llm_summary_on_merge = get_env_value(
        "WEAVE_FORCE_LLM_SUMMARY_ON_MERGE", DEFAULT_FORCE_LLM_SUMMARY_ON_MERGE, int
    )
    args.embedding_func_max_async = get_env_value(
        "WEAVE_EMBEDDING_FUNC_MAX_ASYNC", DEFAULT_EMBEDDING_FUNC_MAX_ASYNC, int
    )
    args.embedding_batch_num = get_env_value(
        "WEAVE_EMBEDDING_BATCH_NUM", DEFAULT_EMBEDDING_BATCH_NUM, int
    )

    # Embedding token limit configuration
    args.embedding_token_limit = get_env_value(
        "WEAVE_EMBEDDING_TOKEN_LIMIT", None, int, special_none=True
    )

    # File upload size limit (in bytes, None for unlimited)
    # Default: 100MB (104857600 bytes)
    args.max_upload_size = get_env_value(
        "WEAVE_MAX_UPLOAD_SIZE", 104857600, int, special_none=True
    )

    # Weave configuration
    # When WEAVE_ENABLE_QUADRUPLE=true the server uses WeaveGraph instead of WeaveEngine,
    # enabling contextual quadruple extraction (h,r,t,rc) and the CGR3 query paradigm.
    args.use_quadruple = get_env_value("WEAVE_ENABLE_QUADRUPLE", False, bool)
    args.cgr3_max_iterations = get_env_value("WEAVE_CGR3_MAX_ITERATIONS", 3, int)
    # Step 4 prototype: route entity/relation extraction through the JSON path
    # (upstream 1.5.x alignment) instead of the delimiter path. Default off.
    args.cg_json_extraction = get_env_value("WEAVE_JSON_EXTRACTION", False, bool)
    args.enable_mcp = get_env_value("WEAVE_ENABLE_MCP", True, bool)
    # Weave — the distributed AI dev-team subsystem (Manager/Architect/Developer/
    # Integrator over a governed graph). Off by default; mounts /weave routes and
    # the board only when true. Requires WEAVE_ENABLE_QUADRUPLE=true to enforce.
    args.enable_weave = get_env_value("WEAVE_ENABLE_TEAM", False, bool)

    # Entity deduplication (Graph-Quality v-next, Topic 1)
    args.dedup_enabled = get_env_value("WEAVE_DEDUP_ENABLED", True, bool)      # master switch
    args.dedup_hard = get_env_value("WEAVE_DEDUP_HARD", 0.93, float)           # auto-merge cosine
    args.dedup_gray = get_env_value("WEAVE_DEDUP_GRAY", 0.85, float)           # queue cosine
    args.dedup_sweep_interval = get_env_value("WEAVE_DEDUP_SWEEP_INTERVAL", 0, int)  # sec; 0 = off
    args.dedup_sweep_batch = get_env_value("WEAVE_DEDUP_SWEEP_BATCH", 10, int)

    # Garbage filtering / constrained node creation (Graph-Quality v-next, Topic 2)
    args.garbage_filter_enabled = get_env_value("WEAVE_GARBAGE_FILTER_ENABLED", True, bool)
    args.garbage_closed_world = get_env_value("WEAVE_GARBAGE_CLOSED_WORLD", False, bool)


    return args


def update_uvicorn_mode_config():
    # If in uvicorn mode and workers > 1, force it to 1 and log warning
    if global_args.workers > 1:
        original_workers = global_args.workers
        global_args.workers = 1
        # Log warning directly here
        logging.warning(
            f">> Forcing workers=1 in uvicorn mode(Ignoring workers={original_workers})"
        )


# Global configuration with lazy initialization
_global_args = None
_initialized = False


def initialize_config(args=None, force=False):
    """Initialize global configuration

    This function allows explicit initialization of the configuration,
    which is useful for programmatic usage, testing, or embedding WeaveEngine
    in other applications.

    Args:
        args: Pre-parsed argparse.Namespace or None to parse from sys.argv
        force: Force re-initialization even if already initialized

    Returns:
        argparse.Namespace: The configured arguments

    Example:
        # Use parsed command line arguments (default)
        initialize_config()

        # Use custom configuration programmatically
        custom_args = argparse.Namespace(
            host='localhost',
            port=8080,
            working_dir='./custom_rag',
            # ... other config
        )
        initialize_config(custom_args)
    """
    global _global_args, _initialized

    if _initialized and not force:
        return _global_args

    _global_args = args if args is not None else parse_args()
    _initialized = True
    return _global_args


def get_config():
    """Get global configuration, auto-initializing if needed

    Returns:
        argparse.Namespace: The configured arguments
    """
    if not _initialized:
        initialize_config()
    return _global_args


class _GlobalArgsProxy:
    """Proxy object that auto-initializes configuration on first access

    This maintains backward compatibility with existing code while
    allowing programmatic control over initialization timing.

    The proxy fully delegates to the underlying argparse.Namespace,
    including support for vars() calls which is used by binding_options
    to extract provider-specific configuration options.
    """

    def __getattribute__(self, name):
        """Override attribute access to support vars() and regular attribute access.

        This method intercepts __dict__ access (used by vars()) and delegates
        to the underlying _global_args namespace, ensuring binding options
        can be properly extracted.
        """
        global _initialized, _global_args

        # Handle __dict__ access for vars() support
        if name == "__dict__":
            if not _initialized:
                initialize_config()
            return vars(_global_args)

        # Handle class-level attributes that should come from the proxy itself
        if name in ("__class__", "__repr__", "__getattribute__", "__setattr__"):
            return object.__getattribute__(self, name)

        # Delegate all other attribute access to the underlying namespace
        if not _initialized:
            initialize_config()
        return getattr(_global_args, name)

    def __setattr__(self, name, value):
        global _initialized, _global_args
        if not _initialized:
            initialize_config()
        setattr(_global_args, name, value)

    def __repr__(self):
        global _initialized, _global_args
        if not _initialized:
            return "<GlobalArgsProxy: Not initialized>"
        return repr(_global_args)


# Create proxy instance for backward compatibility
# Existing code like `from config import global_args` continues to work
# The proxy will auto-initialize on first attribute access
global_args = _GlobalArgsProxy()
