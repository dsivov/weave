"""`weave project` — tell the workspace what it is building.

    weave project register --workspace team --repo git@github.com:acme/thing.git
    weave project show --workspace team

Dev hosts are generic until they know what to build. Registering the repository
once, centrally, is what lets a machine join the fleet with no per-machine
configuration: it reads the project on its next heartbeat and starts from there.

**Nothing is pushed.** This writes state; hosts read it back (A15). A machine
already running when you change the repository picks it up on its next
heartbeat, not because the server told it to — the server cannot tell it
anything, which is precisely what lets that machine sit behind NAT.

**`test_command` is here for the reason W9 exists.** A dev agent that cannot run
the project's tests halts and says so rather than recording "the tests failed" as
a learning. Setting the wrong command is therefore a visible stall instead of a
quiet stream of false findings — but it is still worth setting deliberately,
which is why it is a flag rather than a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex

from weave.cli import _local


def register(groups) -> None:
    parser = groups.add_parser("project", help="what this workspace is building")
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    reg = sub.add_parser("register", help="set the repository and how to build it")
    _local.add_common_arguments(reg)
    reg.add_argument("--repo", default=None,
                     help="clone URL or path, resolvable from the dev hosts")
    reg.add_argument("--base-branch", default=None,
                     help="what each task branch starts from (default: main)")
    reg.add_argument("--image", default=None,
                     help="container image developers run in")
    reg.add_argument("--test-command", default=None,
                     help="how to run the tests, e.g. 'python3 -m pytest -q'")
    reg.add_argument("--setup-command", default=None,
                     help="one-time setup per worktree, e.g. 'bun install'")
    reg.add_argument("--description", default=None)
    reg.add_argument("--by", default="", help="who is registering (default: $USER)")
    reg.add_argument("--json", action="store_true")
    reg.set_defaults(handler=_register)

    show = sub.add_parser("show", help="what this workspace is currently building")
    _local.add_common_arguments(show)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=_show)


def _split(value):
    """A command as typed → argv. `None` means "leave it alone"."""
    return None if value is None else shlex.split(value)


def _register(args: argparse.Namespace) -> int:
    service = _local.project_service(args)
    project = service.set(
        args.workspace,
        repo=args.repo,
        base_branch=args.base_branch,
        image=args.image,
        test_command=_split(args.test_command),
        setup_command=_split(args.setup_command),
        description=args.description,
        by=args.by or os.environ.get("USER", ""),
    )

    if args.json:
        print(json.dumps(project.to_dict(), indent=2))
        return 0

    print(f"workspace '{args.workspace}' is building:")
    _print_project(project)
    print("\nEvery dev host picks this up on its next heartbeat. Nothing is sent "
          "to them (A15).")
    return 0


def _show(args: argparse.Namespace) -> int:
    project = _local.project_service(args).get(args.workspace)
    if args.json:
        print(json.dumps(project.to_dict(), indent=2))
        return 0

    if not project.repo:
        print(f"workspace '{args.workspace}' has no repository registered.\n"
              "  Dev hosts will register and heartbeat, and run nothing.\n"
              "  Fix: weave project register --workspace "
              f"{args.workspace} --repo <url>")
        # A query that answered is a query that succeeded; "nothing registered"
        # is the answer, not an error. `weave doctor` is where checks live.
        return 0

    print(f"workspace '{args.workspace}' is building:")
    _print_project(project)
    return 0


def _print_project(project) -> None:
    print(f"  repo          {project.repo or '(unset)'}")
    print(f"  base branch   {project.base_branch}")
    print(f"  image         {project.image or '(the host default)'}")
    print(f"  test command  {' '.join(project.test_command) or '(unset)'}")
    if project.setup_command:
        print(f"  setup         {' '.join(project.setup_command)}")
    if project.description:
        print(f"  description   {project.description}")
    if project.updated_by:
        print(f"  set by        {project.updated_by}")
