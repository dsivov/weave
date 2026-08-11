"""`weave agents` — how many developers each machine should run.

    weave agents list  --workspace team
    weave agents scale --workspace team --host berlin-01 --count 3
    weave agents up    --workspace team --host berlin-01 --count 3
    weave agents down  --workspace team --host berlin-01

**Read this before reading the code, because the name lies a little.** `up` does
not start anything and `down` does not stop anything. Every one of these writes
a number or a control word into the host's record; the machine reads it on its
next heartbeat and reconciles itself. That indirection is A15, and it is not an
implementation detail to be optimised away later: it is the reason a dev host can
sit behind NAT, on someone's desk, or in a private VPC with no inbound access at
all. **A version of this command that opened a connection to a host would break
every remote fleet, and would look like a latency improvement while doing it.**

So the output says so. Every command prints what will happen and when, and
`list` shows `desired vs running` per host, because the gap between them is the
only honest progress indicator — and a gap that never closes is the symptom of a
daemon that is not heartbeating, which is a completely different problem from
one that is slow to start containers.

`up` and `scale` are the same operation. `up` is spelled separately because it is
what somebody types first, and having to know that "start three developers" is
spelled `scale` is a needless thing to know.
"""

from __future__ import annotations

import argparse
import json
import time

from weave.cli import _local

#: Control words `set_control` accepts, with what each is for.
CONTROLS = {
    "pause": "stop claiming new work; keep running containers",
    "drain": "finish in-flight tasks, then idle",
    "resume": "return a paused or draining host to service",
    "stop": "retire this host — terminal, it cannot be resumed",
}


def register(groups) -> None:
    parser = groups.add_parser(
        "agents", help="how many developers each machine should run")
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    listing = sub.add_parser("list", help="the fleet: desired vs running per host")
    _local.add_common_arguments(listing)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=_list)

    for name, help_text in (
        ("scale", "set how many developers a machine should run"),
        ("up", "same as scale — the word you reach for first"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        _local.add_common_arguments(cmd)
        cmd.add_argument("--host", required=True, help="the machine's id in the fleet")
        cmd.add_argument("--count", type=int, required=True,
                         help="how many developers this machine should run")
        cmd.add_argument("--json", action="store_true")
        cmd.set_defaults(handler=_scale)

    down = sub.add_parser(
        "down", help="retire a machine's developers (scale to 0, or --control stop)")
    _local.add_common_arguments(down)
    down.add_argument("--host", required=True)
    down.add_argument("--control", default="", choices=sorted(CONTROLS),
                      help="a control word instead of scaling to zero")
    down.add_argument("--json", action="store_true")
    down.set_defaults(handler=_down)


def _fleet_line(view) -> str:
    running = len(view.get("workers", []) or [])
    desired = view.get("desired_workers", 0)
    gap = "" if running == desired else "   ← gap"
    age = time.time() - (view.get("last_heartbeat") or 0)
    beat = f"{age:.0f}s ago" if view.get("last_heartbeat") else "never"
    return (f"  {view['id']:<20} desired {desired} · running {running}"
            f"   {view['status']:<9} heartbeat {beat}{gap}")


def _list(args: argparse.Namespace) -> int:
    hosts = _local.host_registry(args).list(args.workspace)
    if args.json:
        print(json.dumps(hosts, indent=2))
        return 0

    if not hosts:
        print(f"no dev hosts registered in '{args.workspace}'.\n"
              "  A machine joins by running, on that machine:\n"
              "    python3 -m weave.devhost --server <url> --workspace "
              f"{args.workspace}")
        # 0, not 1. An empty fleet is a normal state during onboarding, not a
        # failure to list it — and a scripted `set -e` walkthrough of the
        # published steps should not stop here.
        return 0

    print(f"fleet in '{args.workspace}'\n")
    for view in hosts:
        print(_fleet_line(view))

    if any(len(h.get("workers", []) or []) != h.get("desired_workers", 0)
           for h in hosts):
        print("\n  A gap closes on the host's next heartbeat. A gap that never "
              "closes means that\n  machine's daemon is not heartbeating — check "
              "it can reach the server.")
    return 0


def _scale(args: argparse.Namespace) -> int:
    registry = _local.host_registry(args)
    try:
        host = registry.scale(args.workspace, args.host, args.count)
    except KeyError:
        raise SystemExit(
            f"no dev host '{args.host}' in workspace '{args.workspace}'. "
            "A host appears here once it has registered itself; "
            "`weave agents list` shows the fleet.")
    except ValueError as e:
        raise SystemExit(str(e))

    if args.json:
        print(json.dumps(host.to_dict(), indent=2))
        return 0

    print(f"'{args.host}' should now run {args.count} developer(s).")
    print("\nNothing has started yet, and nothing was sent to that machine. It "
          "reads this on its\nnext heartbeat and reconciles itself (A15). "
          "`weave agents list` shows desired vs running.")
    return 0


def _down(args: argparse.Namespace) -> int:
    registry = _local.host_registry(args)

    try:
        if args.control:
            host = registry.set_control(args.workspace, args.host, args.control)
            what = f"control → {args.control} ({CONTROLS[args.control]})"
        else:
            host = registry.scale(args.workspace, args.host, 0)
            what = "should now run 0 developers"
    except KeyError:
        raise SystemExit(
            f"no dev host '{args.host}' in workspace '{args.workspace}'")
    except ValueError as e:
        # `stop` is terminal, and re-registering does not revive it (R73). Saying
        # so beats a bare refusal, because the natural next move is to try again.
        raise SystemExit(str(e))

    if args.json:
        print(json.dumps(host.to_dict(), indent=2))
        return 0

    print(f"'{args.host}': {what}.")
    if args.control == "drain":
        print("\nIn-flight tasks finish; no new work is claimed. Watch "
              "`weave agents list` until running reaches 0.")
    elif args.control == "stop":
        print("\nThis is terminal. Re-registering does not revive a stopped "
              "host — give the machine a new\nhost id if you want it back.")
    else:
        print("\nThe machine stops its containers on its next heartbeat.")
    return 0
