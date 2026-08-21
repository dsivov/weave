"""`weave` — the command line run on the machine the server runs on.

**Why there is a local CLI at all.** Administering users over HTTP requires an
admin, and there are two ordinary ways to have none: a fresh install before
anyone is created, and an install migrated from environment accounts, where
nobody held an admin role because the old scheme had no such concept — the
operator administered by editing a file and restarting. The HTTP surface opens a
bootstrap window only while *no* user exists and closes it for good on the first
one, which is the right rule for a network surface and which leaves a migrated
install with a server it cannot administer. That is precisely the trap M1 was
meant to remove, and P1's fix reintroduced it.

So the escape hatch is local rather than remote. Running this already requires
access to the machine and its storage — strictly more authority than any HTTP
caller has — so it grants nothing new; it just refuses to be reachable from the
network.

**Structure.** One entry point, subcommand groups under it (`weave user …`),
each group a module here. Every group is a thin adapter over the same service
objects the HTTP routers use, never a second implementation of the rules (A9):
password policy, the last-administrator guard and membership grants live in
:class:`weave.server.users.UserService` and are enforced identically whichever
surface you arrive through.

This replaces `python -m weave.server.users`, which P1 grew as an emergency
hatch. Both do not survive the milestone that folds one into the other.
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from weave.cli import agents as _agents
from weave.cli import doctor as _doctor
from weave.cli import docs as _docs
from weave.cli import migrate as _migrate
from weave.cli import project as _project
from weave.cli import roles as _roles
from weave.cli import server as _server
from weave.cli import users as _users
from weave_core.version import __version__

#: Subcommand groups, in the order they appear in `weave --help` — which is the
#: order of the published onboarding, not alphabetical. Someone reading `--help`
#: for the first time is trying to work out what to do next, and the list is the
#: cheapest place to answer that.
_GROUPS = (_server, _doctor, _users, _roles, _project, _agents, _docs, _migrate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weave",
        description="Administer Weave from the machine it runs on.",
    )
    parser.add_argument("--version", action="version", version=f"weave {__version__}")
    groups = parser.add_subparsers(dest="group", required=True, metavar="<group>")
    for module in _GROUPS:
        module.register(groups)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Parse and dispatch. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    return args.handler(args)
