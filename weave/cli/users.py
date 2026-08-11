"""`weave user …` — create and administer accounts from the server machine.

    weave user list
    weave user add alice --role admin --workspaces alpha,beta
    weave user promote alice --role admin
    weave user passwd alice

Every subcommand goes through :class:`weave.server.users.UserService`, the same
object the `/users` routers call. Nothing here re-implements a rule: password
policy, the last-administrator guard and workspace grants are enforced in the
service, so the console and the HTTP surface cannot disagree about who may
become what (A9).

The store is resolved without importing :mod:`weave.server.config`, which parses
``sys.argv`` at import to build the *server's* option set — importing it here
would make this command inherit hundreds of server flags and reject its own
subcommands. Staying independent also keeps the command usable on a machine that
carries the dev-host daemon rather than the server (R75).
"""

from __future__ import annotations

import argparse
import getpass
import os
from typing import Optional

from weave.server.users import JsonUserStore, User, UserError, UserService

#: Where the server keeps its data when nothing says otherwise. Matches the
#: default in `weave/server/config.py`.
DEFAULT_WORKING_DIR = "./weave_storage"


def default_store(working_dir: Optional[str] = None) -> JsonUserStore:
    """The store the server would use, resolved from the environment."""
    base = working_dir or os.environ.get("WEAVE_WORKING_DIR", DEFAULT_WORKING_DIR)
    return JsonUserStore(str(base))


def register(groups) -> None:
    """Attach `weave user` and its subcommands to the top-level parser."""
    parser = groups.add_parser("user", help="create and administer accounts")
    parser.add_argument(
        "--working-dir", default="",
        help="where the store lives (default: $WEAVE_WORKING_DIR, then ./weave_storage)",
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    listing = sub.add_parser("list", help="list every user")
    listing.set_defaults(handler=_list)

    add = sub.add_parser("add", help="create a user")
    add.add_argument("username")
    add.add_argument("--role", default="user")
    add.add_argument("--display-name", default="")
    add.add_argument("--email", default="")
    add.add_argument("--workspaces", default="", help="comma separated")
    add.add_argument("--password", default="", help="prompted for if omitted")
    add.set_defaults(handler=_add)

    promote = sub.add_parser("promote", help="change a user's role")
    promote.add_argument("username")
    promote.add_argument("--role", required=True)
    promote.set_defaults(handler=_promote)

    passwd = sub.add_parser("passwd", help="set a user's password")
    passwd.add_argument("username")
    passwd.add_argument("--password", default="", help="prompted for if omitted")
    passwd.set_defaults(handler=_passwd)


# ── handlers ─────────────────────────────────────────────────────────────────


def _service(args: argparse.Namespace) -> UserService:
    return UserService(default_store(args.working_dir or None))


def _resolve(service: UserService, username: str) -> User:
    user = service.by_username(username)
    if user is None:
        raise SystemExit(f"No user '{username}'.")
    return user


def _ask(supplied: str, prompt: str) -> str:
    return supplied or getpass.getpass(prompt)


def _list(args: argparse.Namespace) -> int:
    users = _service(args).list_users()
    if not users:
        print("No users yet.")
        return 0
    width = max(len(u.username) for u in users)
    for u in users:
        grants = ", ".join(u.workspaces) or "-"
        print(f"{u.username:<{width}}  {u.role:<12} {u.status:<9} {grants}")
    return 0


def _add(args: argparse.Namespace) -> int:
    service = _service(args)
    try:
        user = service.create(
            username=args.username,
            password=_ask(args.password, "Password: "),
            role=args.role,
            display_name=args.display_name,
            email=args.email,
            workspaces=[w.strip() for w in args.workspaces.split(",") if w.strip()],
            granted_by="console",
        )
    except UserError as e:
        raise SystemExit(str(e))
    print(f"Created '{user.username}' with role '{user.role}'.")
    return 0


def _promote(args: argparse.Namespace) -> int:
    service = _service(args)
    user = _resolve(service, args.username)
    try:
        service.update(user.id, role=args.role)
    except UserError as e:
        raise SystemExit(str(e))
    print(f"'{user.username}' is now '{args.role}'.")
    return 0


def _passwd(args: argparse.Namespace) -> int:
    service = _service(args)
    user = _resolve(service, args.username)
    try:
        service.set_password(user.id, _ask(args.password, "New password: "))
    except UserError as e:
        raise SystemExit(str(e))
    print(f"Password set for '{user.username}'.")
    return 0
