"""`weave migrate reviews` — lift task reviews and learnings into nodes (W11).

`weave/model/migrate_reviews.py` has done this correctly since P2, and until now
it was a **library function with no way to invoke it**. Running it meant writing a
script that constructed a task store and a graph by hand — which is exactly what
seeding the demo tenant required, and exactly why the migration sat unrun for
four phases while its tests stayed green.

**A migration an operator cannot invoke is a migration that will not be run.** So
it gets a command, in the phase where the CLI is the deliverable.

    weave migrate reviews --workspace alpha --dry-run
    weave migrate reviews --workspace alpha
    weave migrate reviews --workspace alpha --verify

`--dry-run` first is the documented order, because it reports exactly what a real
run would create — and the number it prints is the number to expect. It creates
no *nodes*; opening the graph store does create an empty graph file where a
workspace had none, which is worth saying rather than rounding to "touches
nothing".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict, Optional

DEFAULT_WORKING_DIR = "./weave_storage"


def register(groups) -> None:
    """Attach `weave migrate` and its subcommands."""
    parser = groups.add_parser("migrate", help="one-off data migrations")
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    reviews = sub.add_parser(
        "reviews",
        help="task reviews/learnings → Review/Insight nodes (R25); idempotent",
    )
    # On the **subcommand**, not the group. `weave migrate reviews --workspace X`
    # is how anyone would write it, and argparse only accepts group-level flags
    # *before* the subcommand — so putting them on the group made the documented
    # form fail to parse. Caught by `tests/test_cli_covers_docs.py`, which is
    # exactly what that test is for.
    reviews.add_argument(
        "--working-dir", default="",
        help="where the store lives (default: $WEAVE_WORKING_DIR, then ./weave_storage)",
    )
    reviews.add_argument("--workspace", default="default")
    reviews.add_argument("--json", action="store_true",
                         help="machine-readable output")
    reviews.add_argument(
        "--dry-run", action="store_true",
        help="report what would be created; creates no nodes",
    )
    reviews.add_argument(
        "--verify", action="store_true",
        help="re-read both sides and compare, instead of migrating",
    )
    reviews.set_defaults(handler=_reviews)


def _working_dir(args: argparse.Namespace) -> str:
    return (args.working_dir
            or os.environ.get("WEAVE_WORKING_DIR", DEFAULT_WORKING_DIR))


async def _open(args: argparse.Namespace):
    """The task store and the workspace graph, without starting a server.

    Only the graph *store* is opened, not the whole engine: the migration reads
    and writes nodes and embeds nothing, so demanding an embedding function would
    make an operator configure a model to move their own data.
    """
    from weave.team.store import JsonWeaveTaskStore
    from weave_core.graph.storage import storage_class
    from weave_core.namespace import NameSpace
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)
    working_dir = _working_dir(args)

    # Through the registry's own resolver (AS6). This used to glue the prefix
    # onto a relative path by hand — a second copy of the convention, which broke
    # the moment the registry stopped using it.
    storage_name = os.environ.get("WEAVE_GRAPH_STORAGE", "NetworkXStorage")
    graph = storage_class(storage_name)(
        namespace=NameSpace.GRAPH_STORE_CHUNK_ENTITY_RELATION,
        workspace=args.workspace,
        embedding_func=None,
        global_config={"working_dir": working_dir},
    )
    await graph.initialize()

    tasks = JsonWeaveTaskStore(os.path.join(working_dir, "weave"))
    return tasks, graph


async def _run_reviews(args: argparse.Namespace) -> Dict[str, Any]:
    from weave.model.migrate_reviews import migrate_workspace, verify_workspace

    tasks, graph = await _open(args)
    try:
        if args.verify:
            return await verify_workspace(args.workspace, tasks, graph)
        report = await migrate_workspace(
            args.workspace, tasks, graph, dry_run=args.dry_run)
        if not args.dry_run:
            saver = getattr(graph, "index_done_callback", None)
            if saver is not None:
                await saver()
        return report
    finally:
        finalize = getattr(graph, "finalize", None)
        if finalize is not None:
            await finalize()


def _reviews(args: argparse.Namespace) -> int:
    try:
        report = asyncio.run(_run_reviews(args))
    except Exception as e:  # noqa: BLE001 - an operator needs the reason, not a trace
        raise SystemExit(f"migration could not run: {type(e).__name__}: {e}")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report.get("complete", True) else 1

    if args.verify:
        print(f"workspace: {report['workspace']}")
        print(f"  checked:    {report['checked']}")
        print(f"  missing:    {len(report['missing'])}")
        print(f"  mismatched: {len(report['mismatched'])}")
        for node_id in report["missing"][:10]:
            print(f"    ✗ missing {node_id}")
        for node_id in report["mismatched"][:10]:
            print(f"    ✗ content differs {node_id}")
        print("\ncomplete." if report["complete"] else "\nincomplete — see above.")
        return 0 if report["complete"] else 1

    label = "would create" if report["dry_run"] else "created"
    print(f"workspace: {report['workspace']}")
    print(f"  tasks scanned:   {report['tasks']}")
    print(f"  reviews found:   {report['reviews_found']}")
    print(f"  learnings found: {report['learnings_found']}")
    print(f"  {label}:{' ' * max(1, 12 - len(label))}{report['nodes_created']}")
    print(f"  already present: {report['nodes_already_present']}")
    if report["dry_run"]:
        # Precise rather than reassuring: opening the graph store creates an
        # empty graph file if the workspace had none, so "nothing was written"
        # would be untrue. No *nodes* are created, which is what a dry run is
        # actually promising.
        print("\nDry run — no nodes were created. Re-run without --dry-run to apply.")
    else:
        print(f"\n{report['nodes_created']} node(s) created. "
              "Re-running is a no-op; use --verify to check by content.")
    return 0
