"""Opening the same stores the server opens, from the machine it runs on.

Every local command — `roles install`, `project register`, `agents scale` — has
to reach the same state the running server reaches. The way to get that wrong is
for each command to construct its own stores from its own idea of the layout, and
then for one of them to drift a directory name and write somewhere nothing reads.

So the layout lives here once. It mirrors `weave/server/app.py`: governance under
`<working-dir>/{ontology,actions,rbac,lifecycle,rules,studio}` and team state
under `<working-dir>/weave`. If that ever changes in `app.py`, this file is the
one other place it is written down, and `tests/test_cli_local_layout.py` fails
when the two disagree rather than leaving an operator to discover it.

**Why local at all.** Same reason `weave user add` is local (see
:mod:`weave.cli`): running this already requires access to the machine and its
storage, which is strictly more authority than any network caller has. It grants
nothing new — it just cannot be reached from the network.

**A4 holds.** These are the file-based adapters, reached through the same
`RecordStore` ports the server uses; no command here constructs a database
client. On a PostgreSQL deployment the local CLI is therefore the *file* path's
tool, and `weave doctor` says so rather than writing to the wrong backend.
"""

from __future__ import annotations

import argparse
import os

from weave.server import DEFAULT_WORKING_DIR, resolve_working_dir  # noqa: F401
from typing import Any, Dict

# The default and the precedence both live in `weave.server` — one definition
# for the CLI and the server alike (W27). This module used to carry its own
# copy, which is the drift its own docstring warns about.

#: `<working-dir>/<name>` for each governance layer, exactly as `app.py` lays it out.
GOVERNANCE_DIRS = {
    "ontology": "ontology",
    "actions": "actions",
    "rbac": "rbac",
    "lifecycle": "lifecycle",
    "rules": "rules",
    "studio": "studio",
    "diagrams": "diagrams",
}

#: Team state (tasks, workers, hosts, projects) shares one directory.
TEAM_DIR = "weave"


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """`--working-dir` and `--workspace`, spelled the same on every command."""
    parser.add_argument(
        "--working-dir", default="",
        help="where Weave keeps its state "
             "(default: $WEAVE_WORKING_DIR, then ./weave_storage)")
    parser.add_argument("--workspace", default="default",
                        help="the workspace to act on")


def working_dir(args: argparse.Namespace) -> str:
    return os.path.abspath(
        resolve_working_dir(getattr(args, "working_dir", "") or None))


def team_dir(args: argparse.Namespace) -> str:
    return os.path.join(working_dir(args), TEAM_DIR)


def governance_services(args: argparse.Namespace) -> Dict[str, Any]:
    """The five governance services, file-backed, as the server builds them."""
    from weave_core.governance.actions import ActionService, JsonActionStore
    from weave_core.governance.lifecycle import JsonLifecycleStore, LifecycleService
    from weave_core.governance.ontology import JsonOntologyStore, OntologyService
    from weave_core.governance.rbac import JsonRbacStore, RbacService
    from weave_core.governance.rules import JsonRuleStore, RulesService

    root = working_dir(args)
    join = lambda name: os.path.join(root, GOVERNANCE_DIRS[name])  # noqa: E731
    return {
        "ontology_service": OntologyService(JsonOntologyStore(join("ontology"))),
        "action_service": ActionService(JsonActionStore(join("actions"))),
        "rbac_service": RbacService(JsonRbacStore(join("rbac"))),
        "lifecycle_service": LifecycleService(JsonLifecycleStore(join("lifecycle"))),
        "rules_service": RulesService(JsonRuleStore(join("rules"))),
    }


async def product_engine(args: argparse.Namespace):
    """The workspace's engine, **wired the way the server wires it** (W37).

    Publishing a document needs the extraction and embedding backends, and a
    locally constructed `WeaveGraph(working_dir=…)` has neither — that was W37,
    where a harness died on `embedding_func is required for vector storage`
    before reading a document. So this borrows the product's own pool rather
    than assembling one: the backends come from the same `WEAVE_EMBEDDING_*` and
    `WEAVE_LLM_*` variables the server reads, and a document published from a
    hook is embedded exactly as one published from a session.

    The caller is responsible for `await pool.shutdown()`; the pool is returned
    alongside the engine for that reason.
    """
    import sys

    # The argv swap covers the imports, not just the call:
    # `weave.server.config.global_args` is a lazy proxy that parses `sys.argv`
    # on first access, and `weave.server.utils` touches it at import time — so
    # importing the app under the CLI's own flags makes the *server's* parser
    # reject them.
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        from weave.server.app import create_app
        from weave.server.config import parse_args

        server_args = parse_args()
        server_args.working_dir = working_dir(args)
        # **Compared before the overrides below, not after.** This function
        # forces `use_quadruple`, so comparing afterwards would report a
        # difference this code had just created — a guard that fires on its own
        # behaviour teaches operators to ignore it (W62).
        assert_matches_server(args, server_args)
        server_args.workers = 1
        server_args.use_quadruple = True
        # This process serves no requests; it needs the engine, not a signer.
        server_args.token_secret = "weave-cli-serves-no-requests"
        app = create_app(server_args)
    finally:
        sys.argv = argv

    pool = getattr(app.state, "workspace_pool", None)
    if pool is None:
        raise SystemExit(
            "this build does not publish app.state.workspace_pool, so the CLI "
            "cannot borrow the product's engine")
    workspace = getattr(args, "workspace", "default") or "default"
    return await pool.get_rag(workspace), pool


def studio_engine(args: argparse.Namespace):
    """The governance ledger — the only writer of a ledger-owned artifact (A8).

    No `rag_resolver`: recording a sign-off as a decision trace needs a live
    engine, and building one locally would mean an embedding model and a
    credential on the operator's machine for a bookkeeping entry. The **version
    and its sign-off are still written** — that is what A8 requires — and the
    decision trace is the part that is absent, which `DiffEngine` already treats
    as optional.
    """
    from weave_core.studio import DiffEngine, JsonStudioStore

    return DiffEngine(
        studio_store=JsonStudioStore(
            os.path.join(working_dir(args), GOVERNANCE_DIRS["studio"])),
        **governance_services(args))


def project_service(args: argparse.Namespace):
    from weave.team.project import JsonWeaveProjectStore, ProjectService

    return ProjectService(JsonWeaveProjectStore(team_dir(args)))


#: The configuration a tool must share with the server to be looking at the same
#: installation. Secrets are deliberately absent — this file sits in the working
#: directory and is compared, never used to authenticate anything.
RUNTIME_KEYS = (
    "kv_storage", "vector_storage", "graph_storage", "doc_status_storage",
    "use_quadruple", "embedding_binding", "embedding_model", "embedding_dim",
)

RUNTIME_FILENAME = "runtime.json"


def write_runtime(working_dir_path: str, server_args) -> None:
    """Record what this server is actually running on (W62).

    **So a tool can tell it is looking at the same installation.** `weave docs
    publish` and `scripts/check_locators.py` build their own engine from the
    environment, and `weave init` writes only the signing secret and two flags —
    so a shell missing the storage variables reads a *file-based* graph while the
    server writes PostgreSQL, and answers confidently from the wrong place.
    Measured: `check_locators` reported `resolved: 0 · dangling: 0` for a
    deployment holding three artifacts.

    Best-effort: a server that cannot write this file still serves. The file is
    an aid to the CLI, not a precondition for the product.
    """
    import json

    try:
        data = {k: getattr(server_args, k, None) for k in RUNTIME_KEYS}
        data["working_dir"] = str(working_dir_path)
        path = os.path.join(str(working_dir_path), RUNTIME_FILENAME)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, default=str)
    except OSError:
        pass


def assert_matches_server(args: argparse.Namespace, server_args) -> None:
    """Refuse when this tool's backend differs from the server's (W62).

    **A clean bill from a different backend is worse than an error**, and this
    project has now produced one three times. If no server has run here there is
    nothing to compare and nothing to say — silence is correct then, because a
    first run is not a mismatch.
    """
    import json

    root = working_dir(args)
    path = os.path.join(root, RUNTIME_FILENAME)
    try:
        with open(path, encoding="utf-8") as fh:
            recorded = json.load(fh)
    except (OSError, ValueError):
        return

    differs = [
        (k, recorded.get(k), getattr(server_args, k, None))
        for k in RUNTIME_KEYS
        if str(recorded.get(k)) != str(getattr(server_args, k, None))
    ]
    if not differs:
        return

    lines = "\n".join(f"    {k}: server {mine!r} · here {theirs!r}"
                      for k, mine, theirs in differs)
    raise SystemExit(
        f"this command is configured differently from the server that last ran in\n"
        f"{root}, so it would read or write a different store:\n\n{lines}\n\n"
        "  Weave's CLI builds its own engine — it is not a client of the running\n"
        "  server — so it needs the same configuration in *this* shell. Source the\n"
        "  same environment the server was started with and run it again.\n\n"
        f"  (Recorded by the server in {RUNTIME_FILENAME}; delete that file if the\n"
        "   deployment has genuinely changed backend.)"
    )


def layout_registry(args: argparse.Namespace):
    """The registry a locator resolves against (W60).

    **Not the same thing as `project_service`, and that was the defect.** The
    team project record says *what this workspace is building*; `ProjectLayout`
    says *where a repository named in a locator actually is*. `weave project
    register` wrote only the first, so `repo · path · rev` had nothing to
    resolve through and every published artifact came back `unregistered`.

    Built from the working directory, exactly as `create_app` builds it, so the
    CLI and the server read one registry rather than two.
    """
    from weave.model.project_layout import (
        JsonProjectLayoutStore, ProjectLayoutRegistry,
    )

    return ProjectLayoutRegistry(JsonProjectLayoutStore(working_dir(args)))


def host_registry(args: argparse.Namespace):
    from weave.devhost.registry import DevHostRegistry, JsonDevHostStore

    return DevHostRegistry(JsonDevHostStore(team_dir(args)))
