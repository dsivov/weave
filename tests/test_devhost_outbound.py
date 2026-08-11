"""The arrow only points one way: hosts dial the server, never the reverse (A15, R63).

`tests/test_weave_devhost.py` already covers the *behaviour* thoroughly — a host
registers, heartbeats, reconciles, survives the server being away, and cannot be
hijacked. This file asserts something those tests structurally cannot: that the
server **has no way** to dial a host, and that nothing has quietly given it one.

The distinction matters because the behavioural tests would all still pass if
someone added a `callback_url` to `DevHost` and a `POST` to it in the supervisor.
Every existing assertion is about what the host does when it heartbeats, and an
extra push channel does not disturb any of them. It would simply mean that a
machine behind NAT — the case the whole design exists for — silently stops
receiving half its instructions, on a code path that works perfectly in the
single-machine dev setup where it would be written and tested.

A15 is the constraint, and the tripwire names it explicitly: *anything that
requires the server to open a connection to a dev host or worker*. So this is the
falsifiable version of it.

**What is deliberately not here:** the reconcile arithmetic, the container env
allowlist, and host ownership. All three are asserted in
`tests/test_weave_devhost.py` (P5), and the P6 plan's separate files for them
would have been a second copy of a rule that already has one — R10 applies to
tests as much as to libraries. The plan's fourth file is this one, because it is
the only one of the four whose property was genuinely unasserted.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from weave.devhost.registry import DevHost, DevHostRegistry, InMemoryDevHostStore

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: The one module allowed to make an outbound HTTP call, and the direction it
#: makes it in: a **host or worker** calling the server. It runs on the machine,
#: not on the hub.
THE_ONLY_CLIENT = "weave/team/worker.py"

#: Ways to open a connection. Not exhaustive of all of Python — exhaustive of
#: what this repository could plausibly reach for, which is the useful scope: a
#: new dependency for HTTP is itself an A11 tripwire and would be noticed.
CONNECTION_CALLS = {
    "urlopen", "Request", "request", "get", "post", "put", "delete", "patch",
}
CONNECTION_MODULES = {"httpx", "requests", "aiohttp", "urllib", "http.client", "socket"}


def _modules_under(*parts: str):
    for package in parts:
        for path in sorted((_REPO / package).glob("**/*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path, str(path.relative_to(_REPO))


# ── 1 · the record carries nothing to dial ───────────────────────────────────


def test_a_host_record_holds_no_address_to_call_back_on():
    """The cheapest guarantee available: you cannot dial what you have no
    address for.

    If a field like `callback_url`, `endpoint` or `port` ever appears here, the
    push channel becomes a small, reasonable-looking change rather than an
    architectural one — and that is the moment remote fleets break.
    """
    dialable = {
        name for name in DevHost.__dataclass_fields__
        if any(word in name.lower()
               for word in ("url", "address", "addr", "endpoint", "host_port",
                            "port", "callback", "webhook", "ip"))
    }
    assert not dialable, (
        "the dev-host record carries something that looks like an address: "
        f"{sorted(dialable)}. A15 says the server never dials a host — a field "
        "to dial is how that stops being true (`machine` is a display name, and "
        "is deliberately not one)."
    )


def test_the_host_identifies_itself_by_name_not_by_route():
    """`machine` is for humans reading the board. Asserted so the next person to
    reach for "somewhere to put the address" finds the answer already written."""
    registry = DevHostRegistry(InMemoryDevHostStore())
    host = asyncio.run(registry.register("ws", "berlin-01", machine="berlin.local"))
    assert host.machine == "berlin.local"
    view = registry.get("ws", "berlin-01")
    assert not any(str(v).startswith(("http://", "https://"))
                   for v in view.values() if isinstance(v, str)), (
        "a URL reached the host record — check what wrote it"
    )


# ── 2 · nothing on the server side can open a connection ─────────────────────


def test_only_the_machine_side_client_makes_an_outbound_call():
    """One HTTP call site in the whole product, and it runs on the machine.

    Scanned rather than asserted about a known list, because the failure this
    guards against is a *new* call site — a list of the ones that exist today
    would pass forever while the seventh module quietly grew one.
    """
    offenders = []
    for path, rel in _modules_under("weave"):
        if rel == THE_ONLY_CLIENT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in CONNECTION_CALLS:
                owner = getattr(func.value, "id", None) or getattr(
                    getattr(func.value, "value", None), "id", "")
                if owner in CONNECTION_MODULES:
                    offenders.append(f"{rel}:{node.lineno} — {owner}.{func.attr}()")

    assert not offenders, (
        "something outside the machine-side client opens a connection:\n  "
        + "\n  ".join(offenders)
        + f"\n\n  Only {THE_ONLY_CLIENT} may, and it runs on the host calling "
        "*in*. A15: the hub never dials out — that is what lets a dev host sit "
        "behind NAT or in a private VPC."
    )


def test_neither_the_registry_nor_the_supervisor_imports_an_http_client():
    """The two modules a push channel would most naturally be written into: the
    thing that knows about hosts, and the thing that supervises them."""
    for rel in ("weave/devhost/registry.py", "weave/team/supervisor.py"):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        clients = imported & {"httpx", "requests", "aiohttp", "urllib", "socket"}
        assert not clients, (
            f"{rel} imports {sorted(clients)} — it has no business holding a "
            "transport. Supervision is state a host reads back, not a call."
        )


# ── 3 · supervision is state, demonstrated ───────────────────────────────────


def test_scaling_a_host_the_server_can_never_reach_still_works():
    """The whole property, end to end, with the network absent rather than mocked.

    Nothing here is patched to fail: there is no transport in this test at all.
    The supervisor writes a number, the host reads it back on the call it makes
    itself, and the two never meet. If scaling ever needed to reach the machine,
    this test could not be written — which is the point of writing it.
    """
    registry = DevHostRegistry(InMemoryDevHostStore())
    asyncio.run(registry.register("ws", "behind-nat", seat="ok"))

    registry.scale("ws", "behind-nat", 3)

    # The machine calls in, unprompted, and learns what it should be running.
    instructions = registry.heartbeat("ws", "behind-nat")
    assert instructions["desired_workers"] == 3
    assert instructions["control"] == "run"

    # ...and reports back what it actually managed to start.
    registry.heartbeat("ws", "behind-nat", workers=["behind-nat-1", "behind-nat-2"])
    view = registry.get("ws", "behind-nat")
    assert view["desired_workers"] == 3 and len(view["workers"]) == 2


def test_a_supervisory_act_records_that_it_travels_by_heartbeat():
    """The supervisor's own account of what it did. A `SupervisoryAct` that
    claimed to have *sent* something would be describing a different
    architecture, and this is where that would show up first."""
    from weave.team.supervisor import Supervisor

    registry = DevHostRegistry(InMemoryDevHostStore())
    asyncio.run(registry.register("ws", "berlin-01", seat="ok"))
    supervisor = Supervisor(workers=None, hosts=registry)

    act = asyncio.run(supervisor.scale_host("ws", "berlin-01", 2, by="alice"))
    assert act.to_dict()["reaches_fleet_via"] == "heartbeat"
