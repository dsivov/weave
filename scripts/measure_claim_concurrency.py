#!/usr/bin/env python
"""Exactly one winner per task under concurrent claims, on every storage path (R2).

An M3 gate criterion, and a measured one rather than a green one: **N simultaneous
claimers, exactly one winner, zero lost writes — on each storage path**. A fleet
race here is invisible until it corrupts work, which is why the claim protocol is
a named tripwire and why this harness *measures* it rather than asserting it once.

    python scripts/measure_claim_concurrency.py                # file path
    python scripts/measure_claim_concurrency.py --n 20 --json
    WEAVE_POSTGRES_HOST=… python scripts/measure_claim_concurrency.py --path postgres

**This harness changes nothing.** It drives `WeaveCoordinator.claim` exactly as a
fleet would and counts outcomes. The protocol, the claim lock and the `touches`
collision rule are untouched — reading them is fine, editing them is a tripwire.

What is counted, and why each matters:

- **winners** — must be exactly 1. Two winners means two developers are editing
  the same task believing they own it.
- **conflicts** — the losers, which must be `N - 1`. A claimer that neither won
  nor conflicted disappeared, and a race that swallows a claimer is worse than
  one that rejects it, because nobody retries.
- **lost writes** — the store's final `assignee` must be the winner's. If the
  winner is recorded as having won but the record says somebody else, the lock
  held and the write did not.
- **errors** — anything else, reported rather than folded into conflicts.

Exit codes: **0** exactly one winner and no losses · **1** a race was observed ·
**2** could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from weave.team.coordinator import WeaveConflict, WeaveCoordinator  # noqa: E402
from weave.team.store import (  # noqa: E402
    InMemoryWeaveTaskStore,
    JsonWeaveTaskStore,
    WeaveTask,
)

WORKSPACE = "claim_harness"
TASK_ID = "TASK-RACE"


def _store(path: str, working_dir: str):
    """A task store per storage path.

    `file` is the deployable single-operator path; `memory` is the control — if
    the race is lost even in memory, the protocol is wrong rather than the
    backend. PostgreSQL goes through the same `RecordStore` port.
    """
    if path == "memory":
        return InMemoryWeaveTaskStore()
    if path == "file":
        return JsonWeaveTaskStore(working_dir)
    if path == "postgres":
        from weave_core.store.postgres import PostgresRecordStore, connection_settings

        class _PgTaskStore(PostgresRecordStore):
            record_type = WeaveTask
            store_name = "weave_tasks_claim_harness"

        return _PgTaskStore(settings=connection_settings())
    raise ValueError(f"unknown storage path '{path}'")


async def race(store, n: int) -> Dict[str, Any]:
    """N claimers, one task, all released at once."""
    store.save(WORKSPACE, WeaveTask(id=TASK_ID, title="contended", status="pending"))
    coordinator = WeaveCoordinator(store)

    start = asyncio.Event()
    outcomes: List[str] = []
    winners: List[str] = []

    async def claimer(index: int) -> None:
        worker = f"worker-{index}"
        await start.wait()          # released together, so they genuinely race
        try:
            await coordinator.claim(WORKSPACE, TASK_ID, worker=worker, role="developer")
            outcomes.append("won")
            winners.append(worker)
        except WeaveConflict:
            outcomes.append("conflict")
        except Exception as e:  # noqa: BLE001 - reported, never folded into conflicts
            outcomes.append(f"error:{type(e).__name__}: {e}")

    tasks = [asyncio.create_task(claimer(i)) for i in range(n)]
    await asyncio.sleep(0)          # let every claimer reach the gate
    start.set()
    await asyncio.gather(*tasks)

    final = store.get(WORKSPACE, TASK_ID)
    counts = Counter(o if o in ("won", "conflict") else "error" for o in outcomes)
    errors = [o for o in outcomes if o.startswith("error")]

    lost_writes = 0
    if len(winners) == 1 and final is not None and final.assignee != winners[0]:
        # The winner was told it won and the record says otherwise.
        lost_writes = 1

    return {
        "claimers": n,
        "winners": counts["won"],
        "conflicts": counts["conflict"],
        "errors": counts["error"],
        "error_detail": errors[:5],
        "final_assignee": final.assignee if final else None,
        "final_status": final.status if final else None,
        "lost_writes": lost_writes,
        "ok": counts["won"] == 1 and counts["conflict"] == n - 1 and lost_writes == 0,
    }


async def _run(args: argparse.Namespace) -> int:
    results: Dict[str, Any] = {}
    for path in args.path:
        try:
            store = _store(path, args.working_dir)
        except Exception as e:  # noqa: BLE001
            results[path] = {"skipped": f"{type(e).__name__}: {e}"}
            continue
        try:
            results[path] = await race(store, args.n)
        finally:
            closer = getattr(store, "close", None)
            if closer is not None:
                closer()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"claim concurrency — N={args.n} simultaneous claimers per path\n")
        for path, r in results.items():
            if "skipped" in r:
                print(f"  {path:<10} SKIPPED — {r['skipped']}")
                continue
            mark = "✓" if r["ok"] else "✗"
            print(
                f"  {path:<10} {mark} winners={r['winners']} "
                f"conflicts={r['conflicts']} errors={r['errors']} "
                f"lost_writes={r['lost_writes']} assignee={r['final_assignee']}"
            )
            for detail in r["error_detail"]:
                print(f"             {detail}")
        print()

    measured = [r for r in results.values() if "skipped" not in r]
    if not measured:
        print("no storage path could be measured", file=sys.stderr)
        return 2
    if all(r["ok"] for r in measured):
        print(f"exactly one winner on every measured path ({len(measured)}).")
        return 0
    print("a race was observed — see the counts above.", file=sys.stderr)
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/measure_claim_concurrency.py",
        description="N simultaneous claims per storage path; report winners, "
                    "conflicts and lost writes (R2, M3 gate).",
    )
    parser.add_argument("--n", type=int, default=20, help="concurrent claimers")
    parser.add_argument(
        "--path", nargs="+", default=["memory", "file"],
        choices=["memory", "file", "postgres"],
        help="storage paths to measure",
    )
    parser.add_argument("--working-dir", default="./claim_harness_storage")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
