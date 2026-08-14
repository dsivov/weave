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


def host_registry(args: argparse.Namespace):
    from weave.devhost.registry import DevHostRegistry, JsonDevHostStore

    return DevHostRegistry(JsonDevHostStore(team_dir(args)))
