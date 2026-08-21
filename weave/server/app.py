"""
WeaveEngine FastAPI Server
"""

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
import asyncio
import os
import logging
import logging.config
import uvicorn
import pipmaster as pm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
import configparser
from ascii_colors import ASCIIColors
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, AsyncExitStack
from dotenv import load_dotenv
from weave.server.utils import (
    get_combined_auth_dependency,
    display_splash_screen,
    check_env_file,
)
from weave.server.config import (
    global_args,
    update_uvicorn_mode_config,
    get_default_host,
)
from weave_core.utils import get_env_value
from weave_core import WeaveEngine, __version__ as core_version
from weave_core.graph.quadruple import WeaveGraph
from weave.server import __api_version__
from weave_core.types import GPTKeywordExtractionFormat
from weave_core.utils import EmbeddingFunc
from weave_core.constants import (
    DEFAULT_LOG_MAX_BYTES,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_EMBEDDING_TIMEOUT,
)
from weave.server.routers.documents import (
    DocumentManager,
    create_document_routes,
)
from weave.server.routers.query import create_query_routes
from weave.server.routers.graph import create_graph_routes
from weave.server.routers.reasoning import create_reasoning_routes
from weave.server.mcp import create_mcp_server
from weave.server.workspace_pool import (
    WORKSPACE_HEADER,
    WorkspacePool,
    WorkspaceProxy,
    get_workspace_middleware,
)

from weave_core.utils import logger, set_verbose_debug
from weave_core.store.locks import (
    get_namespace_data,
    get_default_workspace,
    # set_default_workspace,
    cleanup_keyed_lock,
    finalize_share_data,
)
from fastapi.security import OAuth2PasswordRequestForm
from weave.server.auth import auth_handler

# use the .env that is inside the current folder
# allows to use different .env file for each weave_core instance
# the OS environment variables take precedence over the .env file
load_dotenv(dotenv_path=".env", override=False)


webui_title = os.getenv("WEAVE_WEBUI_TITLE")
webui_description = os.getenv("WEAVE_WEBUI_DESCRIPTION")

# Initialize config parser
config = configparser.ConfigParser()
config.read("config.ini")

# Global authentication configuration


class LLMConfigCache:
    """Smart LLM and Embedding configuration cache class"""

    def __init__(self, args):
        self.args = args

        # Initialize configurations based on binding conditions
        self.openai_llm_options = None
        self.gemini_llm_options = None
        self.gemini_embedding_options = None
        self.ollama_llm_options = None
        self.ollama_embedding_options = None

        # Only initialize and log OpenAI options when using OpenAI-related bindings
        if args.llm_binding in ["openai", "azure_openai"]:
            from weave_core.llm.binding_options import OpenAILLMOptions

            self.openai_llm_options = OpenAILLMOptions.options_dict(args)
            logger.info(f"OpenAI LLM Options: {self.openai_llm_options}")

        if args.llm_binding == "gemini":
            from weave_core.llm.binding_options import GeminiLLMOptions

            self.gemini_llm_options = GeminiLLMOptions.options_dict(args)
            logger.info(f"Gemini LLM Options: {self.gemini_llm_options}")

        # Only initialize and log Ollama LLM options when using Ollama LLM binding
        if args.llm_binding == "ollama":
            try:
                from weave_core.llm.binding_options import OllamaLLMOptions

                self.ollama_llm_options = OllamaLLMOptions.options_dict(args)
                logger.info(f"Ollama LLM Options: {self.ollama_llm_options}")
            except ImportError:
                logger.warning(
                    "OllamaLLMOptions not available, using default configuration"
                )
                self.ollama_llm_options = {}

        # Only initialize and log Ollama Embedding options when using Ollama Embedding binding
        if args.embedding_binding == "ollama":
            try:
                from weave_core.llm.binding_options import OllamaEmbeddingOptions

                self.ollama_embedding_options = OllamaEmbeddingOptions.options_dict(
                    args
                )
                logger.info(
                    f"Ollama Embedding Options: {self.ollama_embedding_options}"
                )
            except ImportError:
                logger.warning(
                    "OllamaEmbeddingOptions not available, using default configuration"
                )
                self.ollama_embedding_options = {}

        # Only initialize and log Gemini Embedding options when using Gemini Embedding binding
        if args.embedding_binding == "gemini":
            try:
                from weave_core.llm.binding_options import GeminiEmbeddingOptions

                self.gemini_embedding_options = GeminiEmbeddingOptions.options_dict(
                    args
                )
                logger.info(
                    f"Gemini Embedding Options: {self.gemini_embedding_options}"
                )
            except ImportError:
                logger.warning(
                    "GeminiEmbeddingOptions not available, using default configuration"
                )
                self.gemini_embedding_options = {}


def check_frontend_build():
    """Check if frontend is built and optionally check if source is up-to-date

    Returns:
        tuple: (assets_exist: bool, is_outdated: bool)
            - assets_exist: True if WebUI build files exist
            - is_outdated: True if source is newer than build (only in dev environment)
    """
    webui_dir = Path(__file__).parent / "webui"
    index_html = webui_dir / "index.html"

    # 1. Check if build files exist
    if not index_html.exists():
        ASCIIColors.yellow("\n" + "=" * 80)
        ASCIIColors.yellow("WARNING: Frontend Not Built")
        ASCIIColors.yellow("=" * 80)
        ASCIIColors.yellow("The WebUI frontend has not been built yet.")
        ASCIIColors.yellow("The API server will start without the WebUI interface.")
        ASCIIColors.yellow(
            "\nTo enable WebUI, build the frontend using these commands:\n"
        )
        ASCIIColors.cyan("    cd weave-ui")
        ASCIIColors.cyan("    bun install --frozen-lockfile")
        ASCIIColors.cyan("    bun run build")
        ASCIIColors.cyan("    cd ..")
        ASCIIColors.yellow("\nThen restart the service.\n")
        ASCIIColors.cyan(
            "Note: Make sure you have Bun installed. Visit https://bun.sh for installation."
        )
        ASCIIColors.yellow("=" * 80 + "\n")
        return (False, False)  # Assets don't exist, not outdated

    # 2. Check if this is a development environment (source directory exists)
    try:
        source_dir = Path(__file__).parent.parent.parent / "weave-ui"
        src_dir = source_dir / "src"

        # Determine if this is a development environment: source directory exists and contains src directory
        if not source_dir.exists() or not src_dir.exists():
            # Production environment, skip source code check
            logger.debug(
                "Production environment detected, skipping source freshness check"
            )
            return (True, False)  # Assets exist, not outdated (prod environment)

        # Development environment, perform source code timestamp check
        logger.debug("Development environment detected, checking source freshness")

        # Source code file extensions (files to check)
        source_extensions = {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",  # TypeScript/JavaScript
            ".css",
            ".scss",
            ".sass",
            ".less",  # Style files
            ".json",
            ".jsonc",  # Configuration/data files
            ".html",
            ".htm",  # Template files
            ".md",
            ".mdx",  # Markdown
        }

        # Key configuration files (in weave-ui root directory)
        key_files = [
            source_dir / "package.json",
            source_dir / "bun.lock",
            source_dir / "vite.config.ts",
            source_dir / "tsconfig.json",
            source_dir / "tailraid.config.js",
            source_dir / "index.html",
        ]

        # Get the latest modification time of source code
        latest_source_time = 0

        # Check source code files in src directory
        for file_path in src_dir.rglob("*"):
            if file_path.is_file():
                # Only check source code files, ignore temporary files and logs
                if file_path.suffix.lower() in source_extensions:
                    mtime = file_path.stat().st_mtime
                    latest_source_time = max(latest_source_time, mtime)

        # Check key configuration files
        for key_file in key_files:
            if key_file.exists():
                mtime = key_file.stat().st_mtime
                latest_source_time = max(latest_source_time, mtime)

        # Get build time
        build_time = index_html.stat().st_mtime

        # Compare timestamps (5 second tolerance to avoid file system time precision issues)
        if latest_source_time > build_time + 5:
            ASCIIColors.yellow("\n" + "=" * 80)
            ASCIIColors.yellow("WARNING: Frontend Source Code Has Been Updated")
            ASCIIColors.yellow("=" * 80)
            ASCIIColors.yellow(
                "The frontend source code is newer than the current build."
            )
            ASCIIColors.yellow(
                "This might happen after 'git pull' or manual code changes.\n"
            )
            ASCIIColors.cyan(
                "Recommended: Rebuild the frontend to use the latest changes:"
            )
            ASCIIColors.cyan("    cd weave-ui")
            ASCIIColors.cyan("    bun install --frozen-lockfile")
            ASCIIColors.cyan("    bun run build")
            ASCIIColors.cyan("    cd ..")
            ASCIIColors.yellow("\nThe server will continue with the current build.")
            ASCIIColors.yellow("=" * 80 + "\n")
            return (True, True)  # Assets exist, outdated
        else:
            logger.info("Frontend build is up-to-date")
            return (True, False)  # Assets exist, up-to-date

    except Exception as e:
        # If check fails, log warning but don't affect startup
        logger.warning(f"Failed to check frontend source freshness: {e}")
        return (True, False)  # Assume assets exist and up-to-date on error


async def _dedup_sweep_loop(workspace_pool, args) -> None:
    """Periodic background dedup sweep (Graph-Quality v-next).

    Every ``WEAVE_DEDUP_SWEEP_INTERVAL`` seconds, drains each workspace's gray-zone review
    queue: reads the per-workspace dedup stores under ``working_dir/dedup``, and for
    any with pending pairs runs the LLM sweep (which applies confirmed merges,
    reversibly). Opt-in and fully logged.
    """
    from weave_core.knowledge.dedup import JsonDedupStore

    interval = int(getattr(args, "dedup_sweep_interval", 0) or 0)
    dedup_dir = os.path.join(str(args.working_dir), "dedup")
    prefix, suffix = "dedup_", ".json"
    while True:
        try:
            await asyncio.sleep(interval)
            if not os.path.isdir(dedup_dir):
                continue
            store = JsonDedupStore(dedup_dir)
            swept = 0
            for fname in sorted(os.listdir(dedup_dir)):
                if not (fname.startswith(prefix) and fname.endswith(suffix)):
                    continue
                ws = fname[len(prefix):-len(suffix)]
                pending = store.list_pending(ws)
                if not pending:
                    continue
                logger.info(
                    f"[dedup-sweep] workspace '{ws}': {len(pending)} pending pair(s) "
                    f"→ adjudicating"
                )
                inst = await workspace_pool.get_rag(ws)
                if hasattr(inst, "run_dedup_sweep"):
                    result = await inst.run_dedup_sweep()
                    swept += 1
                    logger.info(f"[dedup-sweep] workspace '{ws}': {result}")
            if swept == 0:
                logger.debug("[dedup-sweep] tick — no workspaces with pending pairs")
        except asyncio.CancelledError:
            logger.info("[dedup-sweep] scheduler stopped")
            raise
        except Exception as e:  # pragma: no cover - never let the loop die
            logger.error(f"[dedup-sweep] scheduler error: {e}", exc_info=True)


def assert_startup_preconditions(args):
    """Every reason this process may refuse to start, checked before it claims to.

    **These ran inside `create_app`, which runs after the splash screen.** So a
    container printing *"📡 Server Configuration"* and then dying 53 lines later
    told the operator it was starting and then that it could not — and with
    `restart: unless-stopped` it did that twelve times in a minute, until the
    message explaining the refusal had scrolled out of reach (W19).

    A banner is a claim about outcome, and nothing should make it before the
    things that can falsify it have run. So the preconditions live here, and both
    entry points call this **before** `display_splash_screen`.

    Idempotent on purpose: `create_app` still calls it, because
    `get_application()` under gunicorn reaches the app without going through
    either entry point. Guarding one path and not the other is W4's shape.
    """
    from weave.server.auth import InsecureSigningSecret, assert_signing_secret_is_safe
    from weave.server.config import assert_bus_matches_deployment

    # Every token this server issues carries the role RBAC enforces against,
        # so a known secret is not a weak default — it is an open door with a
        # sign on it (S1, A6).
    assert_signing_secret_is_safe(args.token_secret)

    # The event-bus adapter has to match the deployment shape (A7, D-019), and
    # the mismatch it guards is silent: on the in-process bus behind more than
    # one worker, a client on worker 2 never receives an event published on
    # worker 1 — no error, no log, the board just stops updating.
    assert_bus_matches_deployment(
        getattr(args, "event_bus", "inprocess"), int(getattr(args, "workers", 1) or 1)
    )


def refuse_readably(args) -> None:
    """Run the preconditions and present a refusal as an answer (W19).

    **The wrapping belongs here, not in `assert_startup_preconditions`.** Putting
    `SystemExit` there changed what `create_app` raises, and callers legitimately
    catch the typed exceptions — `tests/test_jwt_secret_required.py` expects
    `InsecureSigningSecret` from the app factory, which is the right contract for
    a library boundary.

    An entry point is different: it is talking to a person. `SystemExit` prints
    its argument with no traceback and exits non-zero, so the sentence explaining
    the problem is the first thing read rather than the eleventh line.
    """
    from weave.server.auth import InsecureSigningSecret
    from weave.server.config import BusDeploymentMismatch

    # **Every refusal type a precondition raises must be in this tuple**, and
    # missing one is invisible: the check still fires, so the server still
    # refuses — it just refuses with a traceback instead of a sentence, which is
    # the whole of W19 arriving through a different door.
    #
    # It happened. `BusDeploymentMismatch` is a `RuntimeError`, not a
    # `ValueError`, and it was never listed; the A7 refusal reached operators as
    # eleven frames from the day it was written. It went unnoticed because
    # `QuadrupleUnsupported` — also a `RuntimeError` — *was* listed by name, so
    # the one test that drove this wrapper drove the refusal that worked.
    #
    # `tests/test_startup_refuses_before_it_announces.py` now drives **each**
    # precondition through here and demands a `SystemExit`, so a type left out
    # of this tuple fails rather than degrades.
    try:
        assert_startup_preconditions(args)
    except (InsecureSigningSecret, BusDeploymentMismatch, ValueError) as refusal:
        raise SystemExit(f"\nWeave will not start.\n\n{refusal}\n") from None


#: **Every claim the API description makes, and the routes that make it true.**
#:
#: `(claim, path prefix)`. A claim whose prefix matches no path in the OpenAPI
#: table is a capability the server does not serve, and
#: `tests/test_the_api_describes_what_it_serves.py` fails on it (D-044).
#:
#: The description is *composed* from this list rather than written beside it,
#: which is the point: prose cannot be added to the public contract without
#: declaring what it asserts, and a declaration without routes does not survive
#: the suite.
#:
#: The web UI is deliberately **not** a claim. It is a `Mount`, so it never
#: appears in the OpenAPI paths, and admitting it would mean matching some
#: claims against `app.routes` and others against the document — an exception
#: that would be the obvious place for the next unbacked sentence to hide.
API_CLAIMS = [
    ("the governed team surface", "/weave/"),
    ("the four standing questions", "/ask/"),
    ("the signed ledger", "/studio/"),
]


def _mcp_behind_auth(mcp_app, combined_auth):
    """Put the MCP sub-app behind the dependency that guards the REST routes (W33).

    **The same callable, not an equivalent check.** `combined_auth` is the object
    every `@router.get(..., dependencies=[Depends(combined_auth)])` uses; the
    adapter below pulls the bearer token and API key out of the request and hands
    them to it. Writing a second "may this proceed" here is what produced the
    defect in the first place — an X-API-Key check that existed *only* when an
    API key was configured, so the ordinary JWT deployment had none.

    Two things happen after it allows the request, and both are A6:

    * the **principal** is published to the tools through a contextvar, so
      `invoke_action` enforces RBAC against a real role instead of `None`;
    * the **tenant** is checked against that principal. `WEAVE-WORKSPACE` may
      *select* among the workspaces a principal holds; it may never *grant* one.
      Choosing the tenant from an unauthenticated header is M2's Critical, and
      this is the same shape on a different surface.
    """
    from fastapi import Response
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from weave.server.mcp import _current_principal
    from weave.server.utils import get_principal

    async def guarded(scope, receive, send):
        if scope.get("type") != "http":
            return await mcp_app(scope, receive, send)

        request = Request(scope, receive)
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None

        try:
            await combined_auth(
                request,
                Response(),
                token=token,
                api_key_header_value=request.headers.get("X-API-Key"),
            )
        except HTTPException as refusal:
            response = JSONResponse(
                status_code=refusal.status_code,
                content={"detail": refusal.detail},
            )
            return await response(scope, receive, send)

        # **The tenant boundary is not checked here.** It was, for a day —
        # and then W34 moved the same rule into `combined_auth`, where every
        # authenticated route gets it. Keeping a copy would leave two answers to
        # "may this principal address this workspace", which is the shape of
        # defect this whole sequence has been about. The call above enforces it.
        principal = get_principal(request)
        reset = _current_principal.set(principal)
        try:
            return await mcp_app(scope, receive, send)
        finally:
            _current_principal.reset(reset)

    return guarded


def create_app(args):
    # Re-asserted here because every path into a running server goes through
    # `create_app` — uvicorn's `main()`, gunicorn's `get_application()`, and the
    # tests. The entry points call it earlier so the splash cannot precede it.
    assert_startup_preconditions(args)

    # Check frontend build first and get status
    webui_assets_exist, is_frontend_outdated = check_frontend_build()

    # Create unified API version display with warning symbol if frontend is outdated
    api_version_display = (
        f"{__api_version__}⚠️" if is_frontend_outdated else __api_version__
    )

    # Setup logging
    logger.setLevel(args.log_level)
    set_verbose_debug(args.verbose)

    # Create configuration cache (this will output configuration logs)
    config_cache = LLMConfigCache(args)

    # Verify that bindings are correctly setup
    if args.llm_binding not in [
        "lollms",
        "ollama",
        "openai",
        "azure_openai",
        "aws_bedrock",
        "gemini",
    ]:
        raise Exception("llm binding not supported")

    if args.embedding_binding not in [
        "lollms",
        "ollama",
        "openai",
        "azure_openai",
        "aws_bedrock",
        "jina",
        "gemini",
    ]:
        raise Exception("embedding binding not supported")

    # Set default hosts if not provided
    if args.llm_binding_host is None:
        args.llm_binding_host = get_default_host(args.llm_binding)

    if args.embedding_binding_host is None:
        args.embedding_binding_host = get_default_host(args.embedding_binding)

    # Add SSL validation
    if args.ssl:
        if not args.ssl_certfile or not args.ssl_keyfile:
            raise Exception(
                "SSL certificate and key files must be provided when SSL is enabled"
            )
        if not os.path.exists(args.ssl_certfile):
            raise Exception(f"SSL certificate file not found: {args.ssl_certfile}")
        if not os.path.exists(args.ssl_keyfile):
            raise Exception(f"SSL key file not found: {args.ssl_keyfile}")

    # Check if API key is provided either through env var or args
    api_key = os.getenv("WEAVE_API_KEY") or args.key

    # Initialize document manager with workspace support for data isolation
    doc_manager = DocumentManager(args.input_dir, workspace=args.workspace)

    # Business-rules gate service (per-workspace). Only meaningful in quadruple mode.
    rules_service = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.governance.rules import RulesService, JsonRuleStore

            rules_service = RulesService(
                JsonRuleStore(os.path.join(str(args.working_dir), "rules"))
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Rules service unavailable: {e}")

    # Ontology service (per-workspace typed schema). Only meaningful in quadruple mode.
    ontology_service = None

    def _entity_type_resolver(workspace: str = "") -> list:
        """What the extractor looks for in *this* workspace, asked per run.

        A closure rather than a value: `ontology_service` is bound a few lines
        below, and — far more importantly — the installed ontology changes
        without a restart. Late binding here is not a convenience, it is the
        property A8 requires (P15, D-050).
        """
        from weave.model.entity_types import make_resolver

        return make_resolver(ontology_service)(workspace)

    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.governance.ontology import OntologyService, JsonOntologyStore

            ontology_service = OntologyService(
                JsonOntologyStore(os.path.join(str(args.working_dir), "ontology"))
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Ontology service unavailable: {e}")

    # Action service (per-workspace executable operations). Only in quadruple mode.
    action_service = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.governance.actions import ActionService, JsonActionStore

            action_service = ActionService(
                JsonActionStore(os.path.join(str(args.working_dir), "actions"))
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Action service unavailable: {e}")

    # RBAC service (per-workspace access control). Only in quadruple mode.
    rbac_service = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.governance.rbac import RbacService, JsonRbacStore

            rbac_service = RbacService(
                JsonRbacStore(os.path.join(str(args.working_dir), "rbac"))
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"RBAC service unavailable: {e}")

    # Lifecycle service (per-workspace state machines). Only in quadruple mode.
    lifecycle_service = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.governance.lifecycle import LifecycleService, JsonLifecycleStore

            lifecycle_service = LifecycleService(
                JsonLifecycleStore(os.path.join(str(args.working_dir), "lifecycle"))
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Lifecycle service unavailable: {e}")

    # Ingress service (event front door: connector → mapper → log → bus).
    # Only in quadruple mode — the P1 platform loop.
    # One bus for the whole server, built from the configured adapter (A7).
    # Constructed here rather than inside each subsystem: a module that builds
    # its own `InProcessBus()` silently opts itself out of cross-worker fan-out
    # while the startup check goes on passing, because that check only sees the
    # configured name. The ingress service did precisely that until P3.2.
    from weave.server.config import create_event_bus

    event_bus = create_event_bus(getattr(args, "event_bus", "inprocess"))

    ingress_service = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.events.ingress import JsonIngressLog
            from weave.ingress import IngressService

            ingress_service = IngressService(
                JsonIngressLog(os.path.join(str(args.working_dir), "ingress")),
                event_bus,
                ontology_resolver=(
                    ontology_service.store.load
                    if ontology_service is not None
                    else None
                ),
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Ingress service unavailable: {e}")

    # Flow store + executor (P2 flow engine: event → task/gateway/state → run).
    # Only in quadruple mode — composes the rules/actions/lifecycle services above.
    flow_store = None
    flow_executor = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.flows import (
                FlowExecutor,
                JsonFlowStore,
                JsonRunStore,
            )

            flow_store = JsonFlowStore(os.path.join(str(args.working_dir), "flows"))
            flow_executor = FlowExecutor(
                flow_store,
                JsonRunStore(os.path.join(str(args.working_dir), "flows")),
                rag_resolver=lambda ws: rag,
                rules_service=rules_service,
                action_service=action_service,
                lifecycle_service=lifecycle_service,
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Flow engine unavailable: {e}")

    # Studio diff-engine (P3: propose → assess → apply over ontology/rule/flow/
    # action/diagram, with a signed version ledger). Composes the services above.
    studio_engine = None
    diagram_store = None
    if getattr(args, "use_quadruple", False):
        try:
            from weave_core.studio.diagrams import JsonDiagramStore
            from weave_core.studio import DiffEngine, JsonStudioStore

            # Diagrams live server-side, per workspace, so a team shares one set.
            diagram_store = JsonDiagramStore(os.path.join(str(args.working_dir), "diagrams"))
            studio_engine = DiffEngine(
                studio_store=JsonStudioStore(os.path.join(str(args.working_dir), "studio")),
                rules_service=rules_service,
                ontology_service=ontology_service,
                flow_store=flow_store,
                action_service=action_service,
                diagram_store=diagram_store,
                # Governance as ledger kinds (R35, A8): the wizard changes RBAC
                # and lifecycle by signing a version, never by writing a file
                # the runtime would have to be restarted to read.
                rbac_service=rbac_service,
                lifecycle_service=lifecycle_service,
                rag_resolver=lambda ws: rag,
                llm_resolver=lambda ws: getattr(rag, "llm_model_func", None),
            )
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Studio engine unavailable: {e}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Lifespan context manager for startup and shutdown events"""
        # Store background tasks
        app.state.background_tasks = set()
        dedup_task = None

        async with AsyncExitStack() as stack:
            try:
                # Complete async initialization of the seeded default workspace
                await workspace_pool.finalize_seed(default_workspace)

                # Attach business-rules gates for workspaces with a saved policy,
                # so emit enforces persisted rules immediately after a restart.
                if rules_service is not None:
                    try:
                        for ws in rules_service.store.list_workspaces():
                            inst = await workspace_pool.get_rag(ws)
                            rules_service.attach(inst, ws)
                    except Exception as e:
                        logger.warning(f"Rules gate startup attach failed: {e}")

                # Initialize MCP session manager (if MCP server is configured)
                if hasattr(app.state, "mcp_server"):
                    await stack.enter_async_context(
                        app.state.mcp_server.session_manager.run()
                    )

                if getattr(args, "token_secret", "") == "weave_core-jwt-default-secret":
                    logger.warning(
                        "Using default JWT secret — set WEAVE_TOKEN_SECRET env var for production"
                    )

                # Periodic entity-dedup sweep (opt-in via WEAVE_DEDUP_SWEEP_INTERVAL > 0).
                # Drains each workspace's gray-zone review queue with the LLM and
                # applies confirmed merges. Off by default; every run is logged.
                if (getattr(args, "use_quadruple", False)
                        and getattr(args, "dedup_enabled", True)
                        and getattr(args, "dedup_sweep_interval", 0) > 0):
                    dedup_task = asyncio.create_task(
                        _dedup_sweep_loop(workspace_pool, args)
                    )
                    app.state.background_tasks.add(dedup_task)
                    logger.info(
                        f"Scheduled dedup sweep: every {args.dedup_sweep_interval}s "
                        f"(batch {getattr(args, 'dedup_sweep_batch', 10)})"
                    )

                # The PostgreSQL bus has a listener connection to hold open; the
                # in-process one has nothing to start. Asked by capability rather
                # than by name, so a third adapter would not need this edited.
                starter = getattr(event_bus, "start", None)
                if starter is not None:
                    await starter()

                ASCIIColors.green("\nServer is ready to accept connections! 🚀\n")

                yield

            finally:
                closer = getattr(event_bus, "close", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception as e:  # pragma: no cover - shutdown path
                        logger.warning(f"event bus close failed: {e}")
                if dedup_task is not None:
                    dedup_task.cancel()
                # Clean up all workspace instances
                await workspace_pool.shutdown()

                if "WEAVE_GUNICORN_MODE" not in os.environ:
                    # Only perform cleanup in Uvicorn single-process mode
                    logger.debug("Unvicorn Mode: finalizing shared storage...")
                    finalize_share_data()
                else:
                    # In Gunicorn mode with preload_app=True, cleanup is handled by on_exit hooks
                    logger.debug(
                        "Gunicorn Mode: postpone shared storage finalization to master process"
                    )

    # Initialize FastAPI
    #
    # **The description is composed from declared claims, not written** (D-044).
    # It used to say *"Providing API for WeaveEngine core, Web UI and Ollama
    # Model Emulation"* — and the server serves no Ollama-shaped route at all.
    # The emulation router was deliberately excluded at P0 (12 of 15 routers
    # carried; `ollama_api.py` dropped as "a compatibility surface for a product
    # Weave is not", and the one route group that answered without passing
    # governance, A6). **The 723-line module stayed behind; the sentence
    # advertising it did not** — because exclusions are enforced on files, and
    # claims live in strings inside files that were copied.
    #
    # A stale name misleads about what a thing is called. This misled about what
    # the product does, on the public contract, where a reader can act on it.
    base_description = "Providing API for " + ", ".join(t for t, _ in API_CLAIMS)
    swagger_description = (
        base_description
        + (" (API-Key Enabled)" if api_key else "")
        + "\n\n[View ReDoc documentation](/redoc)"
    )
    app_kwargs = {
        "title": "WeaveEngine Server API",
        "description": swagger_description,
        "version": __api_version__,
        "openapi_url": "/openapi.json",  # Explicitly set OpenAPI schema URL
        "docs_url": None,  # Disable default docs, we'll create custom endpoint
        "redoc_url": "/redoc",  # Explicitly set redoc URL
        "lifespan": lifespan,
    }

    # Configure Swagger UI parameters
    # Enable persistAuthorization and tryItOutEnabled for better user experience
    app_kwargs["swagger_ui_parameters"] = {
        "persistAuthorization": True,
        "tryItOutEnabled": True,
    }

    app = FastAPI(**app_kwargs)

    # Add custom validation error handler for /query/data endpoint
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        # Check if this is a request to /query/data endpoint
        if request.url.path.endswith("/query/data"):
            # Extract error details
            error_details = []
            for error in exc.errors():
                field_path = " -> ".join(str(loc) for loc in error["loc"])
                error_details.append(f"{field_path}: {error['msg']}")

            error_message = "; ".join(error_details)

            # Return in the expected format for /query/data
            return JSONResponse(
                status_code=400,
                content={
                    "status": "failure",
                    "message": f"Validation error: {error_message}",
                    "data": {},
                    "metadata": {},
                },
            )
        else:
            # For other endpoints, return the default FastAPI validation error
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    def get_cors_origins():
        """Get allowed origins from global_args
        Returns a list of allowed origins, defaults to ["*"] if not set
        """
        origins_str = global_args.cors_origins
        if origins_str == "*":
            return ["*"]
        return [origin.strip() for origin in origins_str.split(",")]

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-New-Token"
        ],  # Expose token renewal header for cross-origin requests
    )

    # Create combined auth dependency for all endpoints
    combined_auth = get_combined_auth_dependency(api_key)

    def get_workspace_from_request(request: Request) -> str | None:
        """
        Extract workspace from HTTP request header or use default.

        This enables multi-workspace API support by checking the custom
        'WEAVE-WORKSPACE' header. If not present, falls back to the
        server's default workspace configuration.

        Args:
            request: FastAPI Request object

        Returns:
            Workspace identifier (may be empty string for global namespace)
        """
        # Check custom header first. Starlette's Headers mapping is
        # case-insensitive, so this read was never the broken one — but the name
        # still comes from the single constant, so there is nothing here to fall
        # out of step with the middleware again.
        workspace = request.headers.get(WORKSPACE_HEADER, "").strip()

        if not workspace:
            workspace = None

        return workspace

    # Create working directory if it doesn't exist
    Path(args.working_dir).mkdir(parents=True, exist_ok=True)

    def create_optimized_openai_llm_func(
        config_cache: LLMConfigCache, args, llm_timeout: int
    ):
        """Create optimized OpenAI LLM function with pre-processed configuration"""

        async def optimized_openai_alike_model_complete(
            prompt,
            system_prompt=None,
            history_messages=None,
            keyword_extraction=False,
            **kwargs,
        ) -> str:
            from weave_core.llm.openai import openai_complete_if_cache

            keyword_extraction = kwargs.pop("keyword_extraction", None)
            if keyword_extraction:
                kwargs["response_format"] = GPTKeywordExtractionFormat
            if history_messages is None:
                history_messages = []

            # Use pre-processed configuration to avoid repeated parsing
            kwargs["timeout"] = llm_timeout
            if config_cache.openai_llm_options:
                kwargs.update(config_cache.openai_llm_options)

            return await openai_complete_if_cache(
                args.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                base_url=args.llm_binding_host,
                api_key=args.llm_binding_api_key,
                **kwargs,
            )

        return optimized_openai_alike_model_complete

    def create_optimized_azure_openai_llm_func(
        config_cache: LLMConfigCache, args, llm_timeout: int
    ):
        """Create optimized Azure OpenAI LLM function with pre-processed configuration"""

        async def optimized_azure_openai_model_complete(
            prompt,
            system_prompt=None,
            history_messages=None,
            keyword_extraction=False,
            **kwargs,
        ) -> str:
            from weave_core.llm.azure_openai import azure_openai_complete_if_cache

            keyword_extraction = kwargs.pop("keyword_extraction", None)
            if keyword_extraction:
                kwargs["response_format"] = GPTKeywordExtractionFormat
            if history_messages is None:
                history_messages = []

            # Use pre-processed configuration to avoid repeated parsing
            kwargs["timeout"] = llm_timeout
            if config_cache.openai_llm_options:
                kwargs.update(config_cache.openai_llm_options)

            return await azure_openai_complete_if_cache(
                args.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                base_url=args.llm_binding_host,
                api_key=os.getenv("AZURE_OPENAI_API_KEY", args.llm_binding_api_key),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                **kwargs,
            )

        return optimized_azure_openai_model_complete

    def create_optimized_gemini_llm_func(
        config_cache: LLMConfigCache, args, llm_timeout: int
    ):
        """Create optimized Gemini LLM function with cached configuration"""

        async def optimized_gemini_model_complete(
            prompt,
            system_prompt=None,
            history_messages=None,
            keyword_extraction=False,
            **kwargs,
        ) -> str:
            from weave_core.llm.gemini import gemini_complete_if_cache

            if history_messages is None:
                history_messages = []

            # Use pre-processed configuration to avoid repeated parsing
            kwargs["timeout"] = llm_timeout
            if (
                config_cache.gemini_llm_options is not None
                and "generation_config" not in kwargs
            ):
                kwargs["generation_config"] = dict(config_cache.gemini_llm_options)

            return await gemini_complete_if_cache(
                args.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                api_key=args.llm_binding_api_key,
                base_url=args.llm_binding_host,
                keyword_extraction=keyword_extraction,
                **kwargs,
            )

        return optimized_gemini_model_complete

    def create_llm_model_func(binding: str):
        """
        Create LLM model function based on binding type.
        Uses optimized functions for OpenAI bindings and lazy import for others.
        """
        try:
            if binding == "lollms":
                from weave_core.llm.lollms import lollms_model_complete

                return lollms_model_complete
            elif binding == "ollama":
                from weave_core.llm.ollama import ollama_model_complete

                return ollama_model_complete
            elif binding == "aws_bedrock":
                return bedrock_model_complete  # Already defined locally
            elif binding == "azure_openai":
                # Use optimized function with pre-processed configuration
                return create_optimized_azure_openai_llm_func(
                    config_cache, args, llm_timeout
                )
            elif binding == "gemini":
                return create_optimized_gemini_llm_func(config_cache, args, llm_timeout)
            else:  # openai and compatible
                # Use optimized function with pre-processed configuration
                return create_optimized_openai_llm_func(config_cache, args, llm_timeout)
        except ImportError as e:
            raise Exception(f"Failed to import {binding} LLM binding: {e}")

    def create_llm_model_kwargs(binding: str, args, llm_timeout: int) -> dict:
        """
        Create LLM model kwargs based on binding type.
        Uses lazy import for binding-specific options.
        """
        if binding in ["lollms", "ollama"]:
            try:
                from weave_core.llm.binding_options import OllamaLLMOptions

                return {
                    "host": args.llm_binding_host,
                    "timeout": llm_timeout,
                    "options": OllamaLLMOptions.options_dict(args),
                    "api_key": args.llm_binding_api_key,
                }
            except ImportError as e:
                raise Exception(f"Failed to import {binding} options: {e}")
        return {}

    def create_optimized_embedding_function(
        config_cache: LLMConfigCache, binding, model, host, api_key, args
    ) -> EmbeddingFunc:
        """
        Create optimized embedding function and return an EmbeddingFunc instance
        with proper max_token_size inheritance from provider defaults.

        This function:
        1. Imports the provider embedding function
        2. Extracts max_token_size and embedding_dim from provider if it's an EmbeddingFunc
        3. Creates an optimized wrapper that calls the underlying function directly (avoiding double-wrapping)
        4. Returns a properly configured EmbeddingFunc instance

        Configuration Rules:
        - When WEAVE_EMBEDDING_MODEL is not set: Uses provider's default model and dimension
          (e.g., jina-embeddings-v4 with 2048 dims, text-embedding-3-small with 1536 dims)
        - When WEAVE_EMBEDDING_MODEL is set to a custom model: User MUST also set WEAVE_EMBEDDING_DIM
          to match the custom model's dimension (e.g., for jina-embeddings-v3, set WEAVE_EMBEDDING_DIM=1024)

        Note: The embedding_dim parameter is automatically injected by EmbeddingFunc wrapper
        when send_dimensions=True (enabled for Jina and Gemini bindings). This wrapper calls
        the underlying provider function directly (.func) to avoid double-wrapping, so we must
        explicitly pass embedding_dim to the provider's underlying function.
        """

        # Step 1: Import provider function and extract default attributes
        provider_func = None
        provider_max_token_size = None
        provider_embedding_dim = None

        try:
            if binding == "openai":
                from weave_core.llm.openai import openai_embed

                provider_func = openai_embed
            elif binding == "ollama":
                from weave_core.llm.ollama import ollama_embed

                provider_func = ollama_embed
            elif binding == "gemini":
                from weave_core.llm.gemini import gemini_embed

                provider_func = gemini_embed
            elif binding == "jina":
                from weave_core.llm.jina import jina_embed

                provider_func = jina_embed
            elif binding == "azure_openai":
                from weave_core.llm.azure_openai import azure_openai_embed

                provider_func = azure_openai_embed
            elif binding == "aws_bedrock":
                from weave_core.llm.bedrock import bedrock_embed

                provider_func = bedrock_embed
            elif binding == "lollms":
                from weave_core.llm.lollms import lollms_embed

                provider_func = lollms_embed

            # Extract attributes if provider is an EmbeddingFunc
            if provider_func and isinstance(provider_func, EmbeddingFunc):
                provider_max_token_size = provider_func.max_token_size
                provider_embedding_dim = provider_func.embedding_dim
                logger.debug(
                    f"Extracted from {binding} provider: "
                    f"max_token_size={provider_max_token_size}, "
                    f"embedding_dim={provider_embedding_dim}"
                )
        except ImportError as e:
            logger.warning(f"Could not import provider function for {binding}: {e}")

        # Step 2: Apply priority (user config > provider default)
        # For max_token_size: explicit env var > provider default > None
        final_max_token_size = args.embedding_token_limit or provider_max_token_size
        # For embedding_dim: user config (always has value) takes priority
        # Only use provider default if user config is explicitly None (which shouldn't happen)
        final_embedding_dim = (
            args.embedding_dim if args.embedding_dim else provider_embedding_dim
        )

        # Step 3: Create optimized embedding function (calls underlying function directly)
        # Note: When model is None, each binding will use its own default model
        async def optimized_embedding_function(texts, embedding_dim=None):
            try:
                if binding == "lollms":
                    from weave_core.llm.lollms import lollms_embed

                    # Get real function, skip EmbeddingFunc wrapper if present
                    actual_func = (
                        lollms_embed.func
                        if isinstance(lollms_embed, EmbeddingFunc)
                        else lollms_embed
                    )
                    # lollms embed_model is not used (server uses configured vectorizer)
                    # Only pass base_url and api_key
                    return await actual_func(texts, base_url=host, api_key=api_key)
                elif binding == "ollama":
                    from weave_core.llm.ollama import ollama_embed

                    # Get real function, skip EmbeddingFunc wrapper if present
                    actual_func = (
                        ollama_embed.func
                        if isinstance(ollama_embed, EmbeddingFunc)
                        else ollama_embed
                    )

                    # Use pre-processed configuration if available
                    if config_cache.ollama_embedding_options is not None:
                        ollama_options = config_cache.ollama_embedding_options
                    else:
                        from weave_core.llm.binding_options import OllamaEmbeddingOptions

                        ollama_options = OllamaEmbeddingOptions.options_dict(args)

                    # Pass embed_model only if provided, let function use its default (bge-m3:latest)
                    kwargs = {
                        "texts": texts,
                        "host": host,
                        "api_key": api_key,
                        "options": ollama_options,
                    }
                    if model:
                        kwargs["embed_model"] = model
                    return await actual_func(**kwargs)
                elif binding == "azure_openai":
                    from weave_core.llm.azure_openai import azure_openai_embed

                    actual_func = (
                        azure_openai_embed.func
                        if isinstance(azure_openai_embed, EmbeddingFunc)
                        else azure_openai_embed
                    )
                    # Pass model only if provided, let function use its default otherwise
                    kwargs = {"texts": texts, "api_key": api_key}
                    if model:
                        kwargs["model"] = model
                    return await actual_func(**kwargs)
                elif binding == "aws_bedrock":
                    from weave_core.llm.bedrock import bedrock_embed

                    actual_func = (
                        bedrock_embed.func
                        if isinstance(bedrock_embed, EmbeddingFunc)
                        else bedrock_embed
                    )
                    # Pass model only if provided, let function use its default otherwise
                    kwargs = {"texts": texts}
                    if model:
                        kwargs["model"] = model
                    return await actual_func(**kwargs)
                elif binding == "jina":
                    from weave_core.llm.jina import jina_embed

                    actual_func = (
                        jina_embed.func
                        if isinstance(jina_embed, EmbeddingFunc)
                        else jina_embed
                    )
                    # Pass model only if provided, let function use its default (jina-embeddings-v4)
                    kwargs = {
                        "texts": texts,
                        "embedding_dim": embedding_dim,
                        "base_url": host,
                        "api_key": api_key,
                    }
                    if model:
                        kwargs["model"] = model
                    return await actual_func(**kwargs)
                elif binding == "gemini":
                    from weave_core.llm.gemini import gemini_embed

                    actual_func = (
                        gemini_embed.func
                        if isinstance(gemini_embed, EmbeddingFunc)
                        else gemini_embed
                    )

                    # Use pre-processed configuration if available
                    if config_cache.gemini_embedding_options is not None:
                        gemini_options = config_cache.gemini_embedding_options
                    else:
                        from weave_core.llm.binding_options import GeminiEmbeddingOptions

                        gemini_options = GeminiEmbeddingOptions.options_dict(args)

                    # Pass model only if provided, let function use its default (gemini-embedding-001)
                    kwargs = {
                        "texts": texts,
                        "base_url": host,
                        "api_key": api_key,
                        "embedding_dim": embedding_dim,
                        "task_type": gemini_options.get(
                            "task_type", "RETRIEVAL_DOCUMENT"
                        ),
                    }
                    if model:
                        kwargs["model"] = model
                    return await actual_func(**kwargs)
                else:  # openai and compatible
                    from weave_core.llm.openai import openai_embed

                    actual_func = (
                        openai_embed.func
                        if isinstance(openai_embed, EmbeddingFunc)
                        else openai_embed
                    )
                    # Pass model only if provided, let function use its default (text-embedding-3-small)
                    kwargs = {
                        "texts": texts,
                        "base_url": host,
                        "api_key": api_key,
                        "embedding_dim": embedding_dim,
                    }
                    if model:
                        kwargs["model"] = model
                    return await actual_func(**kwargs)
            except ImportError as e:
                raise Exception(f"Failed to import {binding} embedding: {e}")

        # Step 4: Wrap in EmbeddingFunc and return
        embedding_func_instance = EmbeddingFunc(
            embedding_dim=final_embedding_dim,
            func=optimized_embedding_function,
            max_token_size=final_max_token_size,
            send_dimensions=False,  # Will be set later based on binding requirements
            model_name=model,
        )

        # Log final embedding configuration
        logger.info(
            f"Embedding config: binding={binding} model={model} "
            f"embedding_dim={final_embedding_dim} max_token_size={final_max_token_size}"
        )

        return embedding_func_instance

    llm_timeout = get_env_value("WEAVE_LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT, int)
    embedding_timeout = get_env_value(
        "WEAVE_EMBEDDING_TIMEOUT", DEFAULT_EMBEDDING_TIMEOUT, int
    )

    async def bedrock_model_complete(
        prompt,
        system_prompt=None,
        history_messages=None,
        keyword_extraction=False,
        **kwargs,
    ) -> str:
        # Lazy import
        from weave_core.llm.bedrock import bedrock_complete_if_cache

        keyword_extraction = kwargs.pop("keyword_extraction", None)
        if keyword_extraction:
            kwargs["response_format"] = GPTKeywordExtractionFormat
        if history_messages is None:
            history_messages = []

        # Use global temperature for Bedrock
        kwargs["temperature"] = get_env_value("WEAVE_BEDROCK_LLM_TEMPERATURE", 1.0, float)

        return await bedrock_complete_if_cache(
            args.llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            **kwargs,
        )

    # Create embedding function with optimized configuration and max_token_size inheritance
    import inspect

    # Create the EmbeddingFunc instance (now returns complete EmbeddingFunc with max_token_size)
    embedding_func = create_optimized_embedding_function(
        config_cache=config_cache,
        binding=args.embedding_binding,
        model=args.embedding_model,
        host=args.embedding_binding_host,
        api_key=args.embedding_binding_api_key,
        args=args,
    )

    # Get embedding_send_dim from centralized configuration
    embedding_send_dim = args.embedding_send_dim

    # Check if the underlying function signature has embedding_dim parameter
    sig = inspect.signature(embedding_func.func)
    has_embedding_dim_param = "embedding_dim" in sig.parameters

    # Determine send_dimensions value based on binding type
    # Jina and Gemini REQUIRE dimension parameter (forced to True)
    # OpenAI and others: controlled by WEAVE_EMBEDDING_SEND_DIM environment variable
    if args.embedding_binding in ["jina", "gemini"]:
        # Jina and Gemini APIs require dimension parameter - always send it
        send_dimensions = has_embedding_dim_param
        dimension_control = f"forced by {args.embedding_binding.title()} API"
    else:
        # For OpenAI and other bindings, respect WEAVE_EMBEDDING_SEND_DIM setting
        send_dimensions = embedding_send_dim and has_embedding_dim_param
        if send_dimensions or not embedding_send_dim:
            dimension_control = "by env var"
        else:
            dimension_control = "by not hasparam"

    # Set send_dimensions on the EmbeddingFunc instance
    embedding_func.send_dimensions = send_dimensions

    logger.info(
        f"Send embedding dimension: {send_dimensions} {dimension_control} "
        f"(dimensions={embedding_func.embedding_dim}, has_param={has_embedding_dim_param}, "
        f"binding={args.embedding_binding})"
    )

    # Log max_token_size source
    if embedding_func.max_token_size:
        source = (
            "env variable"
            if args.embedding_token_limit
            else f"{args.embedding_binding} provider default"
        )
        logger.info(
            f"Embedding max_token_size: {embedding_func.max_token_size} (from {source})"
        )
    else:
        logger.info(
            "Embedding max_token_size: None (Embedding token limit is disabled)."
        )

    # Configure rerank function based on args.rerank_bindingparameter
    rerank_model_func = None
    if args.rerank_binding != "null":
        from weave_core.llm.rerank import cohere_rerank, jina_rerank, ali_rerank

        # Map rerank binding to corresponding function
        rerank_functions = {
            "cohere": cohere_rerank,
            "jina": jina_rerank,
            "aliyun": ali_rerank,
        }

        # Select the appropriate rerank function based on binding
        selected_rerank_func = rerank_functions.get(args.rerank_binding)
        if not selected_rerank_func:
            logger.error(f"Unsupported rerank binding: {args.rerank_binding}")
            raise ValueError(f"Unsupported rerank binding: {args.rerank_binding}")

        # Get default values from selected_rerank_func if args values are None
        if args.rerank_model is None or args.rerank_binding_host is None:
            sig = inspect.signature(selected_rerank_func)

            # Set default model if args.rerank_model is None
            if args.rerank_model is None and "model" in sig.parameters:
                default_model = sig.parameters["model"].default
                if default_model != inspect.Parameter.empty:
                    args.rerank_model = default_model

            # Set default base_url if args.rerank_binding_host is None
            if args.rerank_binding_host is None and "base_url" in sig.parameters:
                default_base_url = sig.parameters["base_url"].default
                if default_base_url != inspect.Parameter.empty:
                    args.rerank_binding_host = default_base_url

        async def server_rerank_func(
            query: str, documents: list, top_n: int = None, extra_body: dict = None
        ):
            """Server rerank function with configuration from environment variables"""
            # Prepare kwargs for rerank function
            kwargs = {
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "api_key": args.rerank_binding_api_key,
                "model": args.rerank_model,
                "base_url": args.rerank_binding_host,
            }

            # Add Cohere-specific parameters if using cohere binding
            if args.rerank_binding == "cohere":
                # Enable chunking if configured (useful for models with token limits like ColBERT)
                kwargs["enable_chunking"] = (
                    os.getenv("WEAVE_RERANK_ENABLE_CHUNKING", "false").lower() == "true"
                )
                kwargs["max_tokens_per_doc"] = int(
                    os.getenv("WEAVE_RERANK_MAX_TOKENS_PER_DOC", "4096")
                )

            return await selected_rerank_func(**kwargs, extra_body=extra_body)

        rerank_model_func = server_rerank_func
        logger.info(
            f"Reranking is enabled: {args.rerank_model or 'default model'} using {args.rerank_binding} provider"
        )
    else:
        logger.info("Reranking is disabled")

    # Initialize RAG with unified configuration.
    # Use WeaveGraph when WEAVE_ENABLE_QUADRUPLE=true for contextual quadruple
    # extraction (h,r,t,rc) and the CGR3 iterative reasoning paradigm.
    use_quadruple = getattr(args, "use_quadruple", False)
    rag_cls = WeaveGraph if use_quadruple else WeaveEngine
    if use_quadruple:
        logger.info(
            "Weave mode ENABLED — using WeaveGraph for contextual "
            "quadruple extraction and CGR3 reasoning."
        )

    # Build kwargs for workspace pool (everything except 'workspace')
    rag_kwargs = dict(
        working_dir=args.working_dir,
        llm_model_func=create_llm_model_func(args.llm_binding),
        llm_model_name=args.llm_model,
        llm_model_max_async=args.max_async,
        summary_max_tokens=args.summary_max_tokens,
        summary_context_size=args.summary_context_size,
        chunk_token_size=int(args.chunk_size),
        chunk_overlap_token_size=int(args.chunk_overlap_size),
        llm_model_kwargs=create_llm_model_kwargs(
            args.llm_binding, args, llm_timeout
        ),
        embedding_func=embedding_func,
        default_llm_timeout=llm_timeout,
        default_embedding_timeout=embedding_timeout,
        kv_storage=args.kv_storage,
        graph_storage=args.graph_storage,
        vector_storage=args.vector_storage,
        doc_status_storage=args.doc_status_storage,
        vector_db_storage_cls_kwargs={
            "cosine_better_than_threshold": args.cosine_threshold
        },
        enable_llm_cache_for_entity_extract=args.enable_llm_cache_for_extract,
        enable_llm_cache=args.enable_llm_cache,
        rerank_model_func=rerank_model_func,
        max_parallel_insert=args.max_parallel_insert,
        max_graph_nodes=args.max_graph_nodes,
        addon_params={
            "language": args.summary_language,
            # The static entry stays for library callers and as the last word
            # if the resolver yields nothing; the resolver is what the extraction
            # path actually asks, once per run (P15, A8).
            "entity_types": args.entity_types,
            "entity_types_resolver": _entity_type_resolver,
        },
    )

    # Per-task LLM roles (upstream 1.5.x alignment): build role-bound LLM callables
    # from EXTRACT_/QUERY_ config and attach them to each WeaveGraph the pool
    # creates. Each role reuses the default func unless it's explicitly reconfigured.
    from types import SimpleNamespace

    def _build_role_llm(role_cfg):
        if not role_cfg:
            return None
        if (role_cfg["binding"] == args.llm_binding
                and role_cfg["model"] == args.llm_model
                and role_cfg["host"] == args.llm_binding_host
                and role_cfg["api_key"] == args.llm_binding_api_key):
            return None  # identical to the default → reuse it
        shim = SimpleNamespace(
            llm_model=role_cfg["model"],
            llm_binding_host=role_cfg["host"],
            llm_binding_api_key=role_cfg["api_key"],
        )
        b = role_cfg["binding"]
        if b in ("openai", "openai-ollama"):
            return create_optimized_openai_llm_func(config_cache, shim, llm_timeout)
        if b == "azure_openai":
            return create_optimized_azure_openai_llm_func(config_cache, shim, llm_timeout)
        if b == "gemini":
            return create_optimized_gemini_llm_func(config_cache, shim, llm_timeout)
        logger.warning(
            "Per-role LLM not supported for binding '%s'; role reuses the default model.", b
        )
        return None

    _post_create = None
    if use_quadruple:
        _role_extract = _build_role_llm(getattr(args, "llm_role_extract", None))
        _role_query = _build_role_llm(getattr(args, "llm_role_query", None))
        if _role_extract is not None or _role_query is not None:
            def _post_create(rag_instance):
                if hasattr(rag_instance, "attach_llm_roles"):
                    rag_instance.attach_llm_roles(extract=_role_extract, query=_role_query)
            logger.info(
                "Weave per-task LLM roles active — extract=%s, query=%s",
                args.llm_role_extract["model"], args.llm_role_query["model"],
            )

    # Create workspace pool and proxy for multi-tenant support
    workspace_pool = WorkspacePool(rag_cls=rag_cls, rag_kwargs=rag_kwargs, post_create=_post_create)
    default_workspace = args.workspace if args.workspace else "default"

    # Seed the default workspace synchronously so the proxy can resolve
    # attributes during route registration (e.g. OllamaAPI.__init__).
    # Full async init happens in the lifespan.
    workspace_pool.seed(default_workspace)

    # Set the contextvar default so proxy lookups during setup find the seed
    from weave.server.workspace_pool import _current_workspace
    _current_workspace.set(default_workspace)

    # rag is a proxy that delegates to the correct workspace instance
    # based on the WEAVE-WORKSPACE header (set by middleware via contextvars)
    rag = WorkspaceProxy(workspace_pool)

    # Add workspace middleware — resolves WEAVE-WORKSPACE header per request
    WorkspaceMiddleware = get_workspace_middleware(workspace_pool, default_workspace)
    app.add_middleware(WorkspaceMiddleware)

    # Document the workspace header in OpenAPI docs.
    # The actual routing is handled by the middleware above; this dependency
    # only makes the header visible in Swagger UI. The name comes from the same
    # constant the middleware reads, so what is published and what is honoured
    # cannot drift — they did once, and every request silently answered from the
    # default workspace until a test asserted the two agreed.
    async def workspace_header_doc(
        weave_workspace: str = Header(
            default=default_workspace,
            alias=WORKSPACE_HEADER,
            description="Workspace/tenant name. Each workspace has isolated storage (graph, vectors, documents). "
            "Only a-z, A-Z, 0-9, and _ are allowed.",
        ),
    ):
        pass

    app.dependency_overrides.setdefault(workspace_header_doc, workspace_header_doc)
    app.router.dependencies.append(Depends(workspace_header_doc))

    # Add routes
    app.include_router(
        create_document_routes(
            rag,
            doc_manager,
            api_key,
        )
    )
    app.include_router(create_query_routes(rag, api_key, args.top_k))
    app.include_router(create_graph_routes(rag, api_key))

    # Weave routes (CGR3 query, edge context, entity context edges).
    # Available regardless of use_quadruple — endpoints return HTTP 503
    # when plain WeaveEngine is running so clients can detect capability.
    cgr3_max_iterations = getattr(args, "cgr3_max_iterations", 3)
    app.include_router(
        create_reasoning_routes(
            rag,
            api_key=api_key,
            cgr3_max_iterations=cgr3_max_iterations,
            top_k=args.top_k,
        )
    )

    # Business Rules API (manage + dry-run the per-workspace gate).
    if rules_service is not None:
        from weave.server.routers.rules import create_rules_routes

        app.include_router(create_rules_routes(rag, rules_service, studio_engine=studio_engine, api_key=api_key))

    # Ingress API (webhook front door → durable log → bus → decision quad).
    if ingress_service is not None:
        from weave.server.routers.ingress import create_ingress_routes
        from weave.ingress import DecisionSubscriber

        # The P1 demo loop-closer: every mapped event that names an actor and
        # an object is gated by the workspace rules and recorded as a quad.
        # `rag` is the request-scoped workspace proxy, so the subscriber (which
        # runs inside the request that published the event) hits the right
        # workspace instance.
        ingress_service.bus.subscribe("*", DecisionSubscriber(lambda ws: rag))
        app.include_router(create_ingress_routes(rag, ingress_service, api_key=api_key))

        # P2 flow trigger: every event also starts any flow subscribed to its
        # type. The executor is idempotent on run_id, so a re-delivered event
        # never double-starts. Runs after the DecisionSubscriber above.
        if flow_executor is not None and flow_store is not None:
            from weave_core.flows.trigger import FlowTrigger

            ingress_service.bus.subscribe("*", FlowTrigger(flow_store, flow_executor))

    # Flow engine API (author flows, inspect runs, replay).
    if flow_executor is not None and flow_store is not None:
        from weave.server.routers.flows import create_flow_routes

        app.include_router(create_flow_routes(
            rag, flow_store, flow_executor, studio_engine=studio_engine,
            api_key=api_key))

    # Studio API (diff-and-approve authoring + signed version ledger).
    if studio_engine is not None:
        from weave.server.routers.studio import create_studio_routes

        app.include_router(create_studio_routes(rag, studio_engine, api_key=api_key))

    # Diagram API (shared, versioned project diagrams — the P6 artifact kind).
    if studio_engine is not None and diagram_store is not None:
        from weave.server.routers.diagrams import create_diagram_routes

        app.include_router(create_diagram_routes(
            rag, studio_engine, diagram_store, api_key=api_key))

    # Weave API (distributed AI dev-team subsystem). Off by default; mounted only
    # when WEAVE_ENABLE_TEAM=true. Composes the governance services above + the pull
    # scheduler / atomic-claim coordinator.
    if getattr(args, "enable_weave", False):
        from weave.server.routers.team import create_weave_routes
        from weave.team import (
            WeaveCoordinator, JsonWeaveTaskStore,
            WorkerRegistry, JsonWeaveWorkerStore, JsonIntegrationStore,
            ProjectService, JsonWeaveProjectStore)
        # The host registry lives with the daemon it serves, not with the team
        # model — `weave.devhost` is deployable #2 and installs on a developer
        # machine without the server's dependency set (A1, R75).
        from weave.devhost import DevHostRegistry, JsonDevHostStore

        weave_dir = os.path.join(str(args.working_dir), "weave")
        weave_coordinator = WeaveCoordinator(
            JsonWeaveTaskStore(weave_dir),
            lifecycle_service=lifecycle_service, rag_resolver=lambda ws: rag,
            integration_store=JsonIntegrationStore(weave_dir))
        weave_registry = WorkerRegistry(
            JsonWeaveWorkerStore(weave_dir), rag_resolver=lambda ws: rag)
        weave_project_service = ProjectService(JsonWeaveProjectStore(weave_dir))
        weave_host_registry = DevHostRegistry(
            JsonDevHostStore(weave_dir), rag_resolver=lambda ws: rag,
            project_service=weave_project_service, worker_registry=weave_registry)
        app.include_router(create_weave_routes(
            rag, ontology_service=ontology_service, rules_service=rules_service,
            action_service=action_service, rbac_service=rbac_service,
            lifecycle_service=lifecycle_service, studio_engine=studio_engine,
            coordinator=weave_coordinator,
            registry=weave_registry, host_registry=weave_host_registry,
            project_service=weave_project_service, api_key=api_key))

    # Ontology API (manage the per-workspace typed schema).
    if ontology_service is not None:
        from weave.server.routers.ontology import create_ontology_routes

        app.include_router(create_ontology_routes(rag, ontology_service, studio_engine=studio_engine, api_key=api_key))

    # RBAC API (manage the per-workspace access policy).
    if rbac_service is not None:
        from weave.server.routers.rbac import create_rbac_routes

        app.include_router(create_rbac_routes(rag, rbac_service, studio_engine=studio_engine, api_key=api_key))

    # Lifecycle API (manage the per-workspace state machines).
    if lifecycle_service is not None:
        from weave.server.routers.lifecycle import create_lifecycle_routes

        app.include_router(create_lifecycle_routes(rag, lifecycle_service, studio_engine=studio_engine, api_key=api_key))

    # Actions API (manage + invoke the catalog; RBAC- and lifecycle-gated).
    if action_service is not None:
        from weave.server.routers.actions import create_actions_routes

        app.include_router(create_actions_routes(
            rag, action_service, rbac_service=rbac_service,
            lifecycle_service=lifecycle_service, studio_engine=studio_engine, api_key=api_key))

    # Workspace manifest API (role-scoped operating context for agents).
    if getattr(args, "use_quadruple", False):
        try:
            from weave.server.routers.workspaces import create_workspace_routes

            app.include_router(create_workspace_routes(
                rag, ontology_service=ontology_service, action_service=action_service,
                rules_service=rules_service, lifecycle_service=lifecycle_service,
                rbac_service=rbac_service, studio_engine=studio_engine,
                api_key=api_key))
        except Exception as e:  # pragma: no cover - never block server start
            logger.warning(f"Workspace manifest API unavailable: {e}")

    # ── the user store (A14, D-009) ──────────────────────────────────────────
    # The gap this project exists to close. Accounts are persisted records with
    # bcrypt hashes and explicit per-workspace grants — no environment accounts,
    # and no second persistence layer: this is the same RecordStore port the
    # fleet registries use (A4, D-020).
    from weave.server.migrate_accounts import migrate_env_accounts
    from weave.server.routers.users import create_user_routes
    from weave.server.users import JsonUserStore, UserService

    user_service = UserService(JsonUserStore(str(args.working_dir)))
    auth_handler.bind_user_service(user_service)
    app.state.user_service = user_service

    # Any account still configured as an environment string is moved into the
    # store once, on this boot, and then that variable is dead: nothing else in
    # the repository reads it, so the two can never disagree (R16).
    migrate_env_accounts(user_service)

    app.include_router(create_user_routes(user_service, api_key=api_key))

    # The P2 answer surface. `/ask/*` and `/projects/*` mount together because
    # they are two halves of one promise: a question returns nodes, and a node
    # resolves through its locator to a real document. Either alone is half an
    # answer — citable but unreadable, or readable but uncitable.
    #
    # The `ProjectLayout` registry goes through the same `RecordStore` port the
    # user store and the fleet registries use (A4), so registrations follow the
    # deployment's storage path rather than inventing a fourth one.
    from weave.model.project_layout import JsonProjectLayoutStore, ProjectLayoutRegistry
    from weave.server.routers.ask import create_ask_routes
    from weave.server.routers.projects import create_project_routes

    project_registry = ProjectLayoutRegistry(JsonProjectLayoutStore(str(args.working_dir)))
    app.state.project_registry = project_registry

    app.include_router(create_project_routes(project_registry, api_key=api_key))
    app.include_router(create_ask_routes(rag, api_key=api_key))

    # The live surface (P3). SSE is a third adapter over what the bus already
    # carries, not a fourth answer surface (A9) — and it is the *client* holding
    # a connection open, so A15's outbound-only property is untouched.
    #
    # Membership is re-consulted per event rather than captured at connect time:
    # a stream outlives a revocation, and access removed while someone holds one
    # open has to stop it, or revocation means "applies at the next page load".
    from weave.live.presence import PresenceRegistry
    from weave.server.routers.live import create_live_routes

    presence_registry = PresenceRegistry()
    # Set here rather than where the bus is built: `app` does not exist that
    # early in create_app.
    app.state.event_bus = event_bus
    # Published so a measuring script can borrow the **product's** engine rather
    # than construct its own (W37). `scripts/measure_extraction.py` built a bare
    # `WeaveGraph(working_dir=…)` with no embedding function and died before it
    # read a document — and a harness that wires its own backend would produce
    # numbers that are not comparable to anything the server produces.
    app.state.workspace_pool = workspace_pool
    app.state.presence = presence_registry

    def _is_member(username: str, workspace: str) -> bool:
        if not username:
            return False
        user = user_service.by_username(username)
        return bool(user and user.is_active and user.may_access(workspace))

    app.include_router(
        create_live_routes(
            event_bus, presence_registry, api_key=api_key, membership=_is_member,
        )
    )

    # The team-vocabulary wizard (P4). It installs governance by signing ledger
    # versions through the Studio engine — there is no wizard-only write path and
    # no config file, which is what keeps A8 true.
    from weave.server.routers.wizard import create_wizard_routes

    app.include_router(create_wizard_routes(rag, studio_engine, api_key=api_key))

    # Two route groups the source mounted here are deliberately absent, and their
    # absence is the point rather than an omission:
    #
    #   * the web-scraper routes — web ingestion is a non-goal, and the module
    #     left with lxml and playwright (D-008);
    #   * the Ollama model-emulation API — a compatibility surface for a product
    #     Weave is not. The Ollama *connector* is still wired as one of the eight
    #     server-side model backends; what is gone is pretending to be Ollama.
    #
    # Ollama emulation would also have been the one route group that answers a
    # question without passing governance, which A6 does not allow.

    # Workspace management endpoints for multi-tenant API
    @app.get("/workspaces", tags=["workspaces"])
    async def list_workspaces():
        """List all workspaces (tenants) — both initialized and on-disk."""
        import os

        initialized = set(workspace_pool.workspaces)
        # Discover workspaces from working directory (each subdirectory is a workspace).
        # The governance services keep their per-workspace stores in reserved
        # subdirectories of working_dir (rules/ontology/actions/rbac/lifecycle); those
        # are NOT tenants, so exclude them from the workspace list.
        reserved = {"rules", "ontology", "actions", "rbac", "lifecycle", "dedup",
                    "quarantine", "community"}
        storage_dir = str(args.working_dir)
        on_disk = set()
        if os.path.isdir(storage_dir):
            for name in os.listdir(storage_dir):
                full = os.path.join(storage_dir, name)
                if os.path.isdir(full) and not name.startswith(".") and name not in reserved:
                    on_disk.add(name)
        all_workspaces = sorted(initialized | on_disk)
        return {"workspaces": all_workspaces}

    @app.post("/workspaces/{workspace_name}", tags=["workspaces"])
    async def create_workspace(workspace_name: str):
        """Create and initialize a new workspace (tenant).

        The workspace will be initialized with its own isolated storage
        (Neo4j labels, KV namespaces, vector collections).
        """
        try:
            await workspace_pool.get_rag(workspace_name)
            return {
                "status": "success",
                "workspace": workspace_name,
                "message": f"Workspace '{workspace_name}' is ready.",
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize workspace '{workspace_name}': {e}",
            )

    @app.get("/workspaces/{workspace_name}/health", tags=["workspaces"])
    async def workspace_health(workspace_name: str):
        """Check if a specific workspace is initialized and healthy."""
        if workspace_name in workspace_pool._instances:
            return {
                "workspace": workspace_name,
                "status": "healthy",
                "initialized": workspace_name not in workspace_pool._needs_init,
            }
        return {
            "workspace": workspace_name,
            "status": "not_initialized",
            "initialized": False,
        }

    # Swagger UI, pointed at assets that are actually there (U9).
    #
    # This route **unconditionally** named `/static/swagger-ui/*` while the mount
    # for that path is conditional on the directory existing — and the directory
    # is not shipped. So `/docs` returned 200 and its stylesheet and script both
    # 404'd: a page that loads and does nothing, with a healthy `/openapi.json`
    # behind it.
    #
    # The condition was on the wrong side. The page now follows the mount:
    # vendored assets when they are present (genuinely offline-capable, which is
    # what the original comment intended), and FastAPI's defaults when they are
    # not. **Whether Weave should ship those assets is a separate decision** —
    # it matters for an air-gapped install, where the defaults are a CDN the
    # browser cannot reach — and it is not one to take by leaving a broken page
    # behind.
    _swagger_assets = Path(__file__).parent / "static" / "swagger-ui"
    _swagger_local = _swagger_assets.is_dir() and (
        _swagger_assets / "swagger-ui-bundle.js"
    ).is_file()

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        """Swagger UI — local assets when vendored, FastAPI's defaults otherwise."""
        local = {
            "swagger_js_url": "/static/swagger-ui/swagger-ui-bundle.js",
            "swagger_css_url": "/static/swagger-ui/swagger-ui.css",
            "swagger_favicon_url": "/static/swagger-ui/favicon-32x32.png",
        } if _swagger_local else {}
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url="/docs/oauth2-redirect",
            swagger_ui_parameters=app.swagger_ui_parameters,
            **local,
        )

    @app.get("/docs/oauth2-redirect", include_in_schema=False)
    async def swagger_ui_redirect():
        """OAuth2 redirect for Swagger UI"""
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/")
    async def redirect_to_webui():
        """Redirect root path based on WebUI availability.

        The trailing slash is load-bearing (W10). The UI is a Starlette mount at
        ``/webui``, and a mount answers its directory index at ``/webui/`` — a
        request for ``/webui`` with no slash 404s. Redirecting root to the
        slashless form therefore lands a browser on a 404 at the first URL a
        human types. It survived five milestones because ``webui_assets_exist``
        is false wherever the UI has not been built, so this branch had never
        run.
        """
        if webui_assets_exist:
            return RedirectResponse(url="/webui/")
        else:
            return RedirectResponse(url="/docs")

    @app.get("/auth-status")
    async def get_auth_status():
        """Get authentication status and guest token if auth is not configured"""

        if not auth_handler.auth_configured:
            # No users exist yet: hand out a guest token so a fresh install is
            # reachable, and say so plainly rather than implying it is secured.
            guest_token = auth_handler.create_token(
                username="guest", role="guest", metadata={"auth_mode": "disabled"}
            )
            return {
                "auth_configured": False,
                "access_token": guest_token,
                "token_type": "bearer",
                "auth_mode": "disabled",
                "message": "Authentication is disabled. Using guest access.",
                "core_version": core_version,
                "api_version": api_version_display,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }

        return {
            "auth_configured": True,
            "auth_mode": "enabled",
            "core_version": core_version,
            "api_version": api_version_display,
            "webui_title": webui_title,
            "webui_description": webui_description,
        }

    @app.post("/login")
    async def login(form_data: OAuth2PasswordRequestForm = Depends()):
        if not auth_handler.auth_configured:
            # Authentication not configured, return guest token
            guest_token = auth_handler.create_token(
                username="guest", role="guest", metadata={"auth_mode": "disabled"}
            )
            return {
                "access_token": guest_token,
                "token_type": "bearer",
                "auth_mode": "disabled",
                "message": "Authentication is disabled. Using guest access.",
                "core_version": core_version,
                "api_version": api_version_display,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }
        # The credential check is the store's, and it answers with the user or
        # with nothing. A disabled account and a wrong password are the same
        # 401 on purpose: distinguishing them tells an attacker which half they
        # already have.
        user = auth_handler.authenticate(form_data.username, form_data.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Incorrect credentials")

        # The role travels in the token, resolved server-side from the stored
        # record — never read from the request (A6, R15).
        user_token = auth_handler.create_token(
            username=user.username,
            role=user.role,
            metadata={"auth_mode": "enabled", "workspaces": user.workspaces},
        )
        return {
            "access_token": user_token,
            "token_type": "bearer",
            "auth_mode": "enabled",
            "core_version": core_version,
            "api_version": api_version_display,
            "webui_title": webui_title,
            "webui_description": webui_description,
        }

    @app.get(
        "/health",
        dependencies=[Depends(combined_auth)],
        summary="Get system health and configuration status",
        description="Returns comprehensive system status including WebUI availability, configuration, and operational metrics",
        response_description="System health status with configuration details",
        responses={
            200: {
                "description": "Successful response with system status",
                "content": {
                    "application/json": {
                        "example": {
                            "status": "healthy",
                            "webui_available": True,
                            "working_directory": "/path/to/working/dir",
                            "input_directory": "/path/to/input/dir",
                            "configuration": {
                                "llm_binding": "openai",
                                "llm_model": "gpt-4",
                                "embedding_binding": "openai",
                                "embedding_model": "text-embedding-ada-002",
                                "workspace": "default",
                            },
                            "auth_mode": "enabled",
                            "pipeline_busy": False,
                            "core_version": "0.0.1",
                            "api_version": "0.0.1",
                        }
                    }
                },
            }
        },
    )
    async def get_status(request: Request):
        """Get current system status including WebUI availability"""
        try:
            workspace = get_workspace_from_request(request)
            default_workspace = get_default_workspace()
            if workspace is None:
                workspace = default_workspace
            pipeline_status = await get_namespace_data(
                "pipeline_status", workspace=workspace
            )

            if not auth_handler.auth_configured:
                auth_mode = "disabled"
            else:
                auth_mode = "enabled"

            # Cleanup expired keyed locks and get status
            keyed_lock_info = cleanup_keyed_lock()

            return {
                "status": "healthy",
                "webui_available": webui_assets_exist,
                "working_directory": str(args.working_dir),
                "input_directory": str(args.input_dir),
                "configuration": {
                    # LLM configuration binding/host address (if applicable)/model (if applicable)
                    "llm_binding": args.llm_binding,
                    "llm_binding_host": args.llm_binding_host,
                    "llm_model": args.llm_model,
                    # embedding model configuration binding/host address (if applicable)/model (if applicable)
                    "embedding_binding": args.embedding_binding,
                    "embedding_binding_host": args.embedding_binding_host,
                    "embedding_model": args.embedding_model,
                    "summary_max_tokens": args.summary_max_tokens,
                    "summary_context_size": args.summary_context_size,
                    "kv_storage": args.kv_storage,
                    "doc_status_storage": args.doc_status_storage,
                    "graph_storage": args.graph_storage,
                    "vector_storage": args.vector_storage,
                    "enable_llm_cache_for_extract": args.enable_llm_cache_for_extract,
                    "enable_llm_cache": args.enable_llm_cache,
                    "workspace": default_workspace,
                    "max_graph_nodes": args.max_graph_nodes,
                    # Rerank configuration
                    "enable_rerank": rerank_model_func is not None,
                    "rerank_binding": args.rerank_binding,
                    "rerank_model": args.rerank_model if rerank_model_func else None,
                    "rerank_binding_host": args.rerank_binding_host
                    if rerank_model_func
                    else None,
                    # Environment variable status (requested configuration)
                    "summary_language": args.summary_language,
                    "force_llm_summary_on_merge": args.force_llm_summary_on_merge,
                    "max_parallel_insert": args.max_parallel_insert,
                    "cosine_threshold": args.cosine_threshold,
                    "min_rerank_score": args.min_rerank_score,
                    "related_chunk_number": args.related_chunk_number,
                    "max_async": args.max_async,
                    "embedding_func_max_async": args.embedding_func_max_async,
                    "embedding_batch_num": args.embedding_batch_num,
                },
                "auth_mode": auth_mode,
                "pipeline_busy": pipeline_status.get("busy", False),
                "keyed_locks": keyed_lock_info,
                "core_version": core_version,
                "api_version": api_version_display,
                "webui_title": webui_title,
                "webui_description": webui_description,
            }
        except Exception as e:
            logger.error(f"Error getting health status: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    # Custom StaticFiles class for smart caching
    class SmartStaticFiles(StaticFiles):  # Renamed from NoCacheStaticFiles
        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)

            is_html = path.endswith(".html") or response.media_type == "text/html"

            if is_html:
                response.headers["Cache-Control"] = (
                    "no-cache, no-store, must-revalidate"
                )
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            elif (
                "/assets/" in path
            ):  # Assets (JS, CSS, images, fonts) generated by Vite with hash in filename
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            # Add other rules here if needed for non-HTML, non-asset files

            # Ensure correct Content-Type
            if path.endswith(".js"):
                response.headers["Content-Type"] = "application/javascript"
            elif path.endswith(".css"):
                response.headers["Content-Type"] = "text/css"

            return response

    # Mount Swagger UI static files for offline support
    swagger_static_dir = Path(__file__).parent / "static" / "swagger-ui"
    if swagger_static_dir.exists():
        app.mount(
            "/static/swagger-ui",
            StaticFiles(directory=swagger_static_dir),
            name="swagger-ui-static",
        )

    # Conditionally mount WebUI only if assets exist
    if webui_assets_exist:
        static_dir = Path(__file__).parent / "webui"
        static_dir.mkdir(exist_ok=True)
        app.mount(
            "/webui",
            SmartStaticFiles(
                directory=static_dir, html=True, check_dir=True
            ),  # Use SmartStaticFiles
            name="webui",
        )
        logger.info("WebUI assets mounted at /webui")
    else:
        logger.info("WebUI assets not available, /webui route not mounted")

        # Add redirect for /webui when assets are not available
        @app.get("/webui")
        @app.get("/webui/")
        async def webui_redirect_to_docs():
            """Redirect /webui to /docs when WebUI is not available"""
            return RedirectResponse(url="/docs")

    # MCP Server (embedded, same process) — mounted last so named mounts
    # (/webui, /static/swagger-ui) take precedence over the catch-all.
    if getattr(args, "enable_mcp", True):
        mcp_server, mcp_app = create_mcp_server(
            rag=rag,
            api_key=api_key,
            top_k=args.top_k,
            cgr3_max_iterations=cgr3_max_iterations,
            action_service=action_service,
            rbac_service=rbac_service,
            lifecycle_service=lifecycle_service,
            ontology_service=ontology_service,
            rules_service=rules_service,
            diagram_store=diagram_store,
            studio_engine=studio_engine,
        )
        app.state.mcp_server = mcp_server
        # MCP endpoint at POST /mcp — **behind the same auth as every REST
        # route** (W33). `app.mount` attaches a sub-app outside
        # `app.router.dependencies`, so the unwrapped mount was reachable with
        # no credential on a server where REST answered 401.
        app.mount("", _mcp_behind_auth(mcp_app, combined_auth))
        logger.info("MCP server mounted at /mcp (Streamable HTTP, stateless)")
    else:
        logger.info("MCP server disabled (WEAVE_ENABLE_MCP=false)")

    return app


def get_application(args=None):
    """Factory function for creating the FastAPI application"""
    if args is None:
        args = global_args
    return create_app(args)


def configure_logging():
    """Configure logging for uvicorn startup"""

    # Reset any existing handlers to ensure clean configuration
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "weave_core"]:
        logger = logging.getLogger(logger_name)
        logger.handlers = []
        logger.filters = []

    # **Into the working directory, not wherever the operator was standing** (W26).
    # This defaulted to `os.getcwd()`, so a 1.2 MB log appeared in whatever
    # directory `weave up` happened to be run from — including a home directory
    # or a checkout — and a second run from elsewhere started a second one.
    from weave.server import resolve_working_dir

    log_dir = os.getenv("WEAVE_LOG_DIR") or resolve_working_dir()
    log_file_path = os.path.abspath(os.path.join(log_dir, DEFAULT_LOG_FILENAME))

    print(f"\nWeaveEngine log file: {log_file_path}\n")
    # `dirname(log_dir)` created the *parent* of the log directory, which was
    # harmless only while the default was the cwd (it always exists).
    os.makedirs(log_dir, exist_ok=True)

    # Get log file max size and backup count from environment variables
    log_max_bytes = get_env_value("WEAVE_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES, int)
    log_backup_count = get_env_value("WEAVE_LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT, int)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(levelname)s: %(message)s",
                },
                "detailed": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "file": {
                    "formatter": "detailed",
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": log_file_path,
                    "maxBytes": log_max_bytes,
                    "backupCount": log_backup_count,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                # Configure all uvicorn related loggers
                "uvicorn": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                    "filters": ["path_filter"],
                },
                "uvicorn.error": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                },
                "weave_core": {
                    "handlers": ["console", "file"],
                    "level": "INFO",
                    "propagate": False,
                    "filters": ["path_filter"],
                },
            },
            "filters": {
                "path_filter": {
                    "()": "weave_core.utils.WeavePathFilter",
                },
            },
        }
    )


def check_and_install_dependencies():
    """Check and install required dependencies"""
    required_packages = [
        "uvicorn",
        "tiktoken",
        "fastapi",
        # Add other required packages here
    ]

    # These are all declared in `environment.yml` and `deploy/requirements.txt`,
    # so a missing one means the environment was built wrong. Reporting that is
    # useful; installing it at startup is not — a process whose dependency set is
    # not its manifest is exactly what A11 exists to prevent, and the install
    # needs network the server may not have (it did not, in the image). Fail
    # here, loudly, rather than three imports later in a restart loop.
    missing = [p for p in required_packages if not pm.is_installed(p)]
    if missing:
        raise SystemExit(
            "This environment is missing declared dependencies: "
            + ", ".join(missing)
            + ".\n  They are in environment.yml and deploy/requirements.txt — "
            "rebuild the environment rather than\n  expecting the server to "
            "install them at startup (A11)."
        )


def main():
    # Explicitly initialize configuration for clarity
    # (The proxy will auto-initialize anyway, but this makes intent clear)
    from weave.server.config import initialize_config

    initialize_config()

    # Check if running under Gunicorn
    if "GUNICORN_CMD_ARGS" in os.environ:
        # If started with Gunicorn, return directly as Gunicorn will call get_application
        print("Running under Gunicorn - worker management handled by Gunicorn")
        return

    # Advice, not a gate: this used to prompt and exit (W25).
    check_env_file()

    # Check and install dependencies
    check_and_install_dependencies()

    from multiprocessing import freeze_support

    freeze_support()

    # Configure logging before parsing args
    configure_logging()
    update_uvicorn_mode_config()
    # Preconditions before the banner (W19). A splash announcing a configured
    # server, followed by a refusal, tells the operator two contradictory things
    # in the wrong order — and under a restart policy it repeats until the
    # useful line is gone.
    refuse_readably(global_args)

    display_splash_screen(global_args)

    # Note: Signal handlers are NOT registered here because:
    # - Uvicorn has built-in signal handling that properly calls lifespan shutdown
    # - Custom signal handlers can interfere with uvicorn's graceful shutdown
    # - Cleanup is handled by the lifespan context manager's finally block

    # Create application instance directly instead of using factory function
    app = create_app(global_args)

    # Start Uvicorn in single process mode
    uvicorn_config = {
        "app": app,  # Pass application instance directly instead of string path
        "host": global_args.host,
        "port": global_args.port,
        "log_config": None,  # Disable default config
    }

    if global_args.ssl:
        uvicorn_config.update(
            {
                "ssl_certfile": global_args.ssl_certfile,
                "ssl_keyfile": global_args.ssl_keyfile,
            }
        )

    print(
        f"Starting Uvicorn server in single-process mode on {global_args.host}:{global_args.port}"
    )
    uvicorn.run(**uvicorn_config)


if __name__ == "__main__":
    main()
