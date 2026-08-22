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
import pathlib
import sys
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


def _repo_name(repo: str) -> str:
    """The name a locator's `repo` field will hold for this repository.

    **Derived the same way `publish_artifact` derives it** — the repository
    directory's own name — so the two agree by construction rather than by
    coincidence. That they did *not* agree is W60: `publish_artifact` stamped
    `bestbay_helper` while nothing of that name was registered, so every artifact
    resolved to `unregistered` and A5's claim failed end to end.
    """
    text = (repo or "").strip().rstrip("/")
    if not text:
        return ""
    # scp-style (`git@host:acme/thing.git`) and URL forms both end in the name.
    tail = text.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _looks_like_url(repo: str) -> bool:
    return "://" in repo or repo.strip().startswith("git@")


def _register(args: argparse.Namespace) -> int:
    # **W61: a local path that is not there is not a state worth keeping.**
    # Registering used to succeed for a path the server could not see — inside
    # the bundle, for a host path that does not exist in the container — and the
    # failure surfaced one command later, in `weave docs publish`, after the
    # project record had already been written. A clone URL is a different case:
    # it is resolved by a dev host elsewhere, so it is deliberately not checked.
    # Only for values that are unambiguously a filesystem path. A bare token
    # (`thing`) is a repository *name* and a relative one may be resolved from a
    # dev host's own checkout, so neither is ours to reject — the case W61 is
    # about is an absolute path that does not exist on this machine.
    if args.repo and not _looks_like_url(args.repo) and args.repo.startswith(("/", "~")):
        path = pathlib.Path(args.repo).expanduser()
        if not path.is_dir():
            print(f"no such directory: {path}\n"
                  "  `--repo` is a clone URL or a path this machine can reach. If you\n"
                  "  meant a path inside a container, it must be bind-mounted there\n"
                  "  first — the bundled server mounts only its own data volume.",
                  file=sys.stderr)
            return 2

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

    # **W60: register the layout too, or nothing a locator names will resolve.**
    # These are two records for two jobs — the project says what to build, the
    # layout says where a repository named in a locator actually is — and an
    # operator following the documented command only ever wrote the first.
    name = _repo_name(args.repo)
    layout_note = ""
    if name:
        is_url = _looks_like_url(args.repo)
        local = pathlib.Path(args.repo).expanduser()
        local_path = str(local.resolve()) if not is_url and local.is_dir() else ""
        # A layout needs somewhere to look. A bare repository *name* with neither
        # a URL nor a directory on this machine is a legitimate thing to register
        # as a project — a dev host may resolve it from its own checkout — but it
        # cannot be a layout, and saying so beats a warning nobody reads.
        if is_url or local_path:
            try:
                _local.layout_registry(args).register(
                    args.workspace, name,
                    clone_url=args.repo if is_url else "",
                    local_path=local_path,
                    default_rev=args.base_branch or "main",
                    description=args.description or "",
                )
                layout_note = name
            except Exception as e:  # a bad name must not lose the project record
                print(f"warning: the repository layout was not registered ({e}).\n"
                      "  Artifacts published from it will report 'unregistered'\n"
                      "  and their locators will not resolve.", file=sys.stderr)

    if args.json:
        out = project.to_dict()
        out["layout_registered_as"] = layout_note
        print(json.dumps(out, indent=2))
        return 0

    print(f"workspace '{args.workspace}' is building:")
    _print_project(project)
    if layout_note:
        # Named, because it is the string every locator will carry and the one a
        # reader has to recognise in `check_locators` output.
        print(f"  locators say  {layout_note}")
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
