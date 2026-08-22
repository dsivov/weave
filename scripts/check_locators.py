#!/usr/bin/env python
"""Report every artifact node whose locator does not resolve (R24).

The M2 gate requires **zero** dangling locators, and this is what counts them.
Rot has to be detectable on demand rather than discovered by a frustrated reader
following a citation into a 404 — so this runs at the gate and periodically
after it.

    python scripts/check_locators.py --workspace alpha
    python scripts/check_locators.py --workspace alpha --json

Exit codes: **0** clean · **1** dangling locators found · **2** could not run
(no graph, no registry). The three are distinct because a scheduled run must be
able to tell "nothing is broken" from "I could not look" — a check that exits 0
when it did not execute is worse than no check.

Four outcomes per node, and they are counted separately on purpose:

- **resolved** — the locator points at a file that exists at the recorded rev.
- **dangling** — it points at something that is not there. This is the gate's
  number.
- **unregistered** — the repository is not registered in this workspace, so the
  locator cannot be followed *here*. Distinct from dangling: the pointer may be
  perfectly good and the registry incomplete, and telling an operator to fix the
  wrong one wastes their afternoon.
- **no locator** — an artifact node that never carried one. Reported, not
  failed: `Commit` and `Module` nodes reflected from the task chain legitimately
  have none yet, and failing the gate on them would make the gate unpassable for
  a reason unrelated to rot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from weave.server import resolve_working_dir
from weave.model.locator import Locator, LocatorError  # noqa: E402
from weave.model.project_layout import (  # noqa: E402
    JsonProjectLayoutStore,
    NotRegistered,
    ProjectLayoutRegistry,
)

#: The node types A5 calls artifacts — the ones expected to carry a locator.
#:
#: **Read from the ontology, not kept by hand** (W42, P15). This was a literal
#: set of ten against an ontology of eighteen, so eight types were invisible to
#: the rot check: a `Module`, an `Environment` or a `Question` with a broken
#: locator was not merely passing, it was never looked at. Third hand-written
#: list in this phase, after `DEFAULT_ENTITY_TYPES` and `CONTENT_FIELDS`.
#:
#: `PullRequest` and `Worker` genuinely hold no document, so the ontology is
#: filtered to the types that declare a locator property — the ontology already
#: says which those are, and saying it twice is how the two drift.
def artifact_types() -> set:
    """Every ontology object type that declares a locator."""
    try:
        from weave.team import preset

        ontology = preset.load_part("ontology") or {}
        named = {
            o["name"] for o in ontology.get("object_types", [])
            if any(str(p.get("name", "")).startswith("locator_")
                   for p in o.get("properties", []))
        }
        if named:
            return named
    except Exception:
        pass
    return {"PRD", "RFC", "ArchitectureDecisionRecord", "Diagram", "ChangeRequest",
            "Task", "Feature", "Review", "Insight", "Commit"}


ARTIFACT_TYPES = artifact_types()


async def check_workspace(
    workspace: str, graph, registry: ProjectLayoutRegistry
) -> Dict[str, Any]:
    """Walk every artifact node and try to follow its locator."""
    resolved: List[str] = []
    dangling: List[Dict[str, Any]] = []
    unregistered: List[Dict[str, Any]] = []
    without: List[str] = []
    malformed: List[Dict[str, Any]] = []

    for node_id in await graph.get_all_labels() or []:
        node = await graph.get_node(node_id)
        if node is None or (node.get("entity_type") or "") not in ARTIFACT_TYPES:
            continue

        try:
            locator = Locator.from_node_properties(node)
        except LocatorError as e:
            # A partial locator is a defect in its own right: it would resolve
            # against a moving HEAD, or fail far from its cause.
            malformed.append({"node": node_id, "reason": str(e)})
            continue

        if locator is None:
            without.append(node_id)
            continue

        try:
            result = registry.resolve(workspace, locator, want_content=False)
        except NotRegistered:
            unregistered.append({"node": node_id, "repo": locator.repo})
            continue

        if result.get("exists"):
            resolved.append(node_id)
        else:
            dangling.append({
                "node": node_id,
                "locator": locator.to_dict(),
                "reason": result.get("reason", "not found"),
            })

    return {
        "workspace": workspace,
        "resolved": len(resolved),
        "dangling": dangling,
        "unregistered": unregistered,
        "malformed": malformed,
        "without_locator": without,
        # The gate's number. Malformed counts here because a locator that cannot
        # be parsed is a locator that cannot be followed.
        "failures": len(dangling) + len(malformed),
    }


def _report(result: Dict[str, Any]) -> None:
    print(f"workspace: {result['workspace']}")
    print(f"  resolved:        {result['resolved']}")
    print(f"  dangling:        {len(result['dangling'])}")
    print(f"  malformed:       {len(result['malformed'])}")
    print(f"  unregistered:    {len(result['unregistered'])}  (repo not registered here)")
    print(f"  without locator: {len(result['without_locator'])}  (reported, not a failure)")

    for item in result["dangling"]:
        loc = item["locator"]
        print(f"    ✗ {item['node']} → {loc['repo']}:{loc['path']}@{loc['rev']} "
              f"— {item['reason']}")
    for item in result["malformed"]:
        print(f"    ✗ {item['node']} — {item['reason']}")
    for item in result["unregistered"]:
        print(f"    ? {item['node']} — '{item['repo']}' is not registered in this workspace")

    if result["failures"]:
        print(f"\n{result['failures']} locator(s) do not resolve.")
    else:
        print("\n0 dangling locators.")


async def _run(args: argparse.Namespace) -> int:
    try:
        graph = await _open_graph(args)
    except Exception as e:  # noqa: BLE001
        print(f"could not open the graph: {e}", file=sys.stderr)
        return 2

    registry = ProjectLayoutRegistry(JsonProjectLayoutStore(args.working_dir))
    try:
        result = await check_workspace(args.workspace, graph, registry)
    finally:
        finalize = getattr(graph, "finalize", None)
        if finalize is not None:
            await finalize()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _report(result)
    return 1 if result["failures"] else 0


async def _open_graph(args: argparse.Namespace):
    """Open **only** the graph store for one workspace.

    Not the whole engine: constructing a `WeaveGraph` builds vector storages too
    and demands an embedding function, which this check has no use for — it reads
    nodes and follows locators, and nothing here embeds anything. Requiring a
    model to run a rot check would make the check something people skip.

    The backend comes from `WEAVE_GRAPH_STORAGE`, so the check runs against
    whichever storage path the deployment actually uses.
    """
    import os

    from weave_core.graph.storage import STORAGES
    from weave_core.namespace import NameSpace
    from weave_core.store.locks import initialize_share_data

    # The storages take their locks from the shared-data registry, which a server
    # normally sets up at boot. One worker: this is a single-process read.
    initialize_share_data(1)

    # **Refuse a clean bill from the wrong backend** (W62).
    #
    # This picked `NetworkXStorage` whenever `WEAVE_GRAPH_STORAGE` was absent
    # from *this* shell, so against a PostgreSQL deployment holding three
    # artifacts it read an empty file-based graph and printed
    # `resolved: 0 · dangling: 0` — a pass, from a store the server never
    # writes. The working directory was right and the backend was not, which is
    # W42 one layer up.
    storage_name = os.environ.get("WEAVE_GRAPH_STORAGE", "NetworkXStorage")
    _recorded = os.path.join(args.working_dir, "runtime.json")
    if os.path.exists(_recorded):
        try:
            with open(_recorded, encoding="utf-8") as fh:
                _server_graph = json.load(fh).get("graph_storage")
        except (OSError, ValueError):
            _server_graph = None
        if _server_graph and _server_graph != storage_name:
            raise SystemExit(
                f"the server that last ran in {args.working_dir} uses "
                f"{_server_graph}, but this shell is configured for "
                f"{storage_name}.\n\n"
                "  Reading the wrong store would report a clean bill for a graph\n"
                "  it never looked at. Export the same WEAVE_GRAPH_STORAGE (and the\n"
                "  rest of the deployment's configuration) and run it again."
            )
    module = __import__(
        STORAGES[storage_name], fromlist=[storage_name]
    )
    storage_cls = getattr(module, storage_name)

    graph = storage_cls(
        namespace=NameSpace.GRAPH_STORE_CHUNK_ENTITY_RELATION,
        workspace=args.workspace,
        embedding_func=None,
        global_config={"working_dir": args.working_dir},
    )
    await graph.initialize()
    return graph


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/check_locators.py",
        description="Report artifact nodes whose locator does not resolve (R24).",
    )
    parser.add_argument("--workspace", default="default")
    # **The same default the server and the CLI resolve** (W42, D-048).
    #
    # This read `./weave_storage` and no environment variable, so with
    # `WEAVE_WORKING_DIR` exported it reported "resolved: 0 · dangling: 0" from a
    # directory it had never looked in — a clean bill from an empty inspection,
    # on CR-002's own acceptance gate. W27's split default, surviving in a script
    # the sweep missed.
    parser.add_argument("--working-dir", default=resolve_working_dir())
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
