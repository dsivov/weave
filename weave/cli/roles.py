"""`weave roles` — install the governance preset, and hand a role its kit.

Two jobs a new install needs and cannot do over HTTP before it has an admin:

    weave roles install --workspace team --approver alice
    weave roles list
    weave roles kit --role developer --workspace team --out ./dev-kit

**`install` calls the shared installer (R44).** `weave.team.preset.install()` is
the same function `POST /weave/bootstrap` calls, signing every layer through the
governance ledger (A8, D-034). This command does not reimplement it, does not
skip the signature, and does not have a "local mode" that writes faster — a
second installer with different guarantees is exactly the shape D-032 and D-034
were both about.

**`kit` writes what `playbook.role_kit()` returns**, for human roles and agent
roles alike (R52a). There is one generator: a manager's kit and a dev agent's
kit differ in their contents, never in how they are produced. That is A10 in
file form — every role is an ordinary Claude Code session, so every role gets
the same two files, `.mcp.json` and `CLAUDE.md`.

**Regenerating is idempotent (R56)** and is checked, not asserted: identical
inputs produce byte-identical files, and a kit that would not change is reported
as `unchanged` rather than rewritten. An operator who reruns the command to be
sure should see that nothing moved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict, List, Tuple

from weave.cli import _local

#: The two files a role kit is. Both are Claude Code's, not Weave's — which is
#: the point of A10: no bespoke client, so no bespoke config format.
KIT_FILES = (".mcp.json", "CLAUDE.md")


def register(groups) -> None:
    parser = groups.add_parser("roles", help="governance preset and per-role kits")
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    install = sub.add_parser(
        "install", help="install the governance preset into a workspace (signed)")
    _local.add_common_arguments(install)
    install.add_argument(
        "--approver", default="",
        help="who is signing this (default: $USER). Governance is attributed — "
             "'who took away my access' has to be answerable")
    install.add_argument("--reason", default="onboarding: install the Weave governance preset")
    install.add_argument("--json", action="store_true")
    install.set_defaults(handler=_install)

    listing = sub.add_parser("list", help="the roles the preset defines")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=_list)

    kit = sub.add_parser("kit", help="write a role's Claude Code kit to a directory")
    _local.add_common_arguments(kit)
    kit.add_argument("--role", required=True, help="e.g. manager, architect, developer")
    kit.add_argument("--out", default=".", help="where to write .mcp.json and CLAUDE.md")
    kit.add_argument("--server", default="",
                     help="the URL roles will reach (default: $WEAVE_PUBLIC_URL, "
                          "then http://localhost:9800)")
    kit.add_argument("--token", default="",
                     help="the bearer token the session authenticates with "
                          "(from POST /login). Written into .mcp.json, which is "
                          "then mode 0600 — /mcp requires a credential (W33)")
    kit.add_argument("--json", action="store_true")
    kit.set_defaults(handler=_kit)


# ── install ──────────────────────────────────────────────────────────────────


def _install(args: argparse.Namespace) -> int:
    from weave.team import preset

    approver = args.approver or os.environ.get("USER") or ""
    if not approver:
        raise SystemExit(
            "--approver is required (and $USER is unset): installing governance "
            "decides who may do what, and an unattributed policy change makes "
            "'who took away my access' unanswerable")

    problems = preset.validate()
    if problems:
        raise SystemExit("the preset does not validate:\n  " + "\n  ".join(problems))

    try:
        report = asyncio.run(preset.install(
            args.workspace, _local.studio_engine(args),
            approver=approver, reason=args.reason))
    except Exception as e:  # noqa: BLE001 - an operator needs the reason, not a trace
        raise SystemExit(f"install failed: {type(e).__name__}: {e}")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"governance installed into '{args.workspace}', signed by {approver}:")
    for part, kind in preset.LAYERS:
        version = report.get(part)
        if version is not None:
            print(f"  {part:<10} → {kind} v{version}")
    print("\nEach layer is a signed ledger version, so it can be rolled back and "
          "it says who signed it.\nThe rules layer is enforced from now, not "
          "after a restart.")
    return 0


# ── list ─────────────────────────────────────────────────────────────────────


def _list(args: argparse.Namespace) -> int:
    from weave.team import playbook

    roles = playbook.roles()
    if args.json:
        print(json.dumps(roles, indent=2))
        return 0

    print("roles in the Weave preset\n")
    for r in roles:
        runtime = r["runtime"]
        marks = []
        if r.get("human_only"):
            marks.append("human")
        if r.get("optional"):
            marks.append("optional")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(f"  {r['role']:<12} {r['title']:<22} {runtime}{suffix}")
        print(f"               {r['summary']}")
    print("\nEvery one of these is an ordinary Claude Code session (A10). "
          "`weave roles kit --role <name>`\nwrites the two files it needs.")
    return 0


# ── kit ──────────────────────────────────────────────────────────────────────


def _kit_contents(role: str, workspace: str, server: str,
                  token: str = "") -> List[Tuple[str, str]]:
    """The kit as (filename, text) pairs — one generator, every role (R52a)."""
    from weave.team import playbook

    try:
        kit: Dict[str, Any] = playbook.role_kit(role, workspace, server, token)
    except KeyError:
        known = ", ".join(r["role"] for r in playbook.roles())
        raise SystemExit(f"unknown role '{role}'. Known roles: {known}")

    return [
        # `sort_keys` and a trailing newline so a regenerated kit is
        # byte-identical rather than merely equivalent (R56).
        (".mcp.json", json.dumps(kit["mcp_config"], indent=2, sort_keys=True) + "\n"),
        ("CLAUDE.md", kit["claude_md"]),
    ]


def _restrict(path: str) -> None:
    """0600 on a kit file — `.mcp.json` holds a bearer token (W33).

    Applied to both files rather than only the one carrying the credential: a
    rule with an exception is a rule someone has to remember, and `CLAUDE.md`
    being readable buys nothing. Same reasoning, and the same mode, as the
    secret `weave init` writes.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - a filesystem that has no modes
        pass


def _kit(args: argparse.Namespace) -> int:
    server = (args.server
              or os.environ.get("WEAVE_PUBLIC_URL")
              or "http://localhost:9800")
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    token = args.token or os.environ.get("WEAVE_TOKEN", "")

    written, unchanged = [], []
    for name, text in _kit_contents(args.role, args.workspace, server, token):
        path = os.path.join(out, name)
        existing = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        if existing == text:
            # Still assert the mode. A kit regenerated over one written before
            # `/mcp` required a credential would otherwise keep whatever
            # permissions it had, and "unchanged" would be true of the content
            # while false of the thing that matters.
            _restrict(path)
            unchanged.append(name)
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        _restrict(path)
        written.append(name)

    if args.json:
        print(json.dumps({"role": args.role, "workspace": args.workspace,
                          "out": out, "written": written,
                          "unchanged": unchanged}, indent=2))
        return 0

    print(f"kit for '{args.role}' in workspace '{args.workspace}' → {out}")
    for name in KIT_FILES:
        state = "written" if name in written else "unchanged"
        print(f"  {state:<9} {name}")
    print(f"\nPoint a Claude Code session at {out} and start it. "
          "There is no Weave client to install:\nthe kit is the client "
          "configuration (A10).")
    if token:
        print("\n.mcp.json holds a bearer token and is mode 0600. "
              "Do not commit it.")
    else:
        print("\nNo token was supplied, so .mcp.json has no Authorization "
              "header and /mcp will\nrefuse it. Get one from POST /login and "
              "re-run with --token, or paste it in.")
    return 0
