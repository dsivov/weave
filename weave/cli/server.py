"""`weave init` and `weave up` — get a server running from nothing.

    weave init --working-dir ./weave_storage
    weave up   --working-dir ./weave_storage --host 0.0.0.0 --port 9800

**`init` exists because of one refusal.** The server will not start on the
published default token secret — every token it issues carries the role RBAC is
enforced against, so a shared default means anybody who has read the repository
can mint an admin. That refusal is correct and it is also the first thing a new
operator hits, so `init` generates a real secret and puts it somewhere the next
command can find it.

It writes `weave.env` with mode `0600` and **refuses to overwrite one that
already exists.** Regenerating a secret invalidates every issued token, which is
a thing to do deliberately and never as a side effect of re-running a setup
command — `--force` is there for when you mean it.

**`up` is a thin front on `python -m weave.server.app`, not a second server.** It
sets the two feature flags the published steps rely on, loads `weave.env` if it
is there, and hands over. Anything it did that the module did not would be a
difference between the documented way to start Weave and the way it actually
starts, which is the class of problem this whole phase is about (W7).

There is no `weave down`. `up` runs in the foreground and stops on Ctrl-C; a
`down` would need a pidfile, which is background state and a new failure mode
for something a process manager already does properly. (`weave agents down` is a
different thing entirely — it retires a *machine's developers*, and that one
exists.)
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from weave.cli import _local

#: What the server refuses to start on — the value shipped in the repository.
PUBLISHED_DEFAULT_SECRET = "weave_core-jwt-default-secret"

ENV_FILENAME = "weave.env"

#: Flags the published steps assume. Set here so the guide's `weave up` and the
#: guide's `python3 -m weave.server.app` reach the same server.
FEATURE_FLAGS = ("WEAVE_ENABLE_QUADRUPLE", "WEAVE_ENABLE_TEAM")


def register(groups) -> None:
    init = groups.add_parser("init", help="prepare a working directory and its secret")
    init.add_argument("--working-dir", default="",
                      help="where Weave keeps its state "
                           "(default: $WEAVE_WORKING_DIR, then ./weave_storage)")
    init.add_argument("--force", action="store_true",
                      help="regenerate the token secret, invalidating every "
                           "issued token")
    init.set_defaults(handler=_init, workspace="default")

    up = groups.add_parser("up", help="start the Weave server in the foreground")
    up.add_argument("--working-dir", default="")
    up.add_argument("--host", default="0.0.0.0",
                    help="bind address; 0.0.0.0 if anyone will reach this from "
                         "another machine")
    up.add_argument("--port", type=int, default=9800)
    up.set_defaults(handler=_up, workspace="default")


def _env_path(args: argparse.Namespace) -> str:
    return os.path.join(_local.working_dir(args), ENV_FILENAME)


def _init(args: argparse.Namespace) -> int:
    root = _local.working_dir(args)
    os.makedirs(root, exist_ok=True)
    path = _env_path(args)

    if os.path.exists(path) and not args.force:
        print(f"{path} already exists — leaving it alone.\n\n"
              "  Its token secret is what every issued token is signed with, so "
              "regenerating it\n  logs everybody out. Pass --force if that is "
              "what you want.")
        return 0

    secret = secrets.token_urlsafe(48)
    body = (
        "# Written by `weave init`. Source this before `weave up`.\n"
        "#\n"
        "# WEAVE_TOKEN_SECRET signs every token the server issues, and each token\n"
        "# carries the role RBAC is enforced against. Treat it as a credential:\n"
        "# anyone holding it can mint an administrator.\n"
        f"export WEAVE_TOKEN_SECRET='{secret}'\n"
        "export WEAVE_ENABLE_QUADRUPLE=true\n"
        "export WEAVE_ENABLE_TEAM=true\n"
        f"export WEAVE_WORKING_DIR='{root}'\n"
    )
    # 0600 from the moment it exists, rather than written and then chmod'ed —
    # the gap between the two is short and is exactly when it is world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)

    print(f"initialised {root}\n")
    print(f"  {ENV_FILENAME}   a fresh token secret, mode 0600")
    print("\nNext:")
    print(f"  source {path}")
    print("  weave up")
    print("  weave user add <name> --role admin --workspaces <workspace>")
    return 0


def _load_env_file(path: str) -> int:
    """Read `export K='V'` lines from `weave.env` into the environment.

    Deliberately not a dotenv parser: this file is written by `init` and has one
    shape. Anything more permissive would quietly accept a file that `source`
    reads differently, and a token secret that differs between the two is a
    debugging session nobody enjoys.
    """
    if not os.path.exists(path):
        return 0
    loaded = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("export ") or "=" not in line:
                continue
            key, _, value = line[len("export "):].partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            # The shell wins: an operator who exported something meant it.
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded


def _up(args: argparse.Namespace) -> int:
    root = _local.working_dir(args)
    loaded = _load_env_file(_env_path(args))

    secret = os.environ.get("WEAVE_TOKEN_SECRET", "")
    if not secret or secret == PUBLISHED_DEFAULT_SECRET:
        raise SystemExit(
            "WEAVE_TOKEN_SECRET is unset or still the published default, and the "
            "server will refuse\nto start on it — every token it issues carries "
            "the role RBAC is enforced against.\n\n"
            f"  Fix:  weave init --working-dir {root}\n"
            f"        source {os.path.join(root, ENV_FILENAME)}")

    for flag in FEATURE_FLAGS:
        os.environ.setdefault(flag, "true")

    if loaded:
        print(f"loaded {loaded} setting(s) from {ENV_FILENAME}")

    from weave.server import app as server_app

    # Hand over to the module the guide also names, with the arguments it
    # parses. One server, one code path — `up` is a shorter way to type it, not
    # a different way to run it.
    sys.argv = [
        "weave.server.app",
        "--host", args.host,
        "--port", str(args.port),
        "--working-dir", root,
    ]
    server_app.main()
    return 0
