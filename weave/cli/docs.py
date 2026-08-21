"""`weave docs publish` — the second caller, for hooks and CI (P14, CR-002).

The MCP tool is how a *session* publishes what it just authored. This is how a
**commit hook, a CI step or a human** publishes a file that already exists, on a
machine that already has the storage. Both call
`weave.model.artifacts.publish_artifact`; there is no second implementation, and
so no way for the two to disagree about what publishing means.

**Local for the same reason `weave user add` is** (see :mod:`weave.cli`): running
this already requires access to the machine and its working directory, which is
strictly more authority than any network caller has. It grants nothing new.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict

from weave.cli import _local


def register(groups) -> None:
    parser = groups.add_parser("docs", help="publish authored documents into Weave")
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    publish = sub.add_parser(
        "publish",
        help="ingest a document and point an artifact node at it (repo · path · rev)")
    _local.add_common_arguments(publish)
    publish.add_argument("--path", required=True, help="the file to publish")
    publish.add_argument("--artifact", required=True,
                         help="the artifact id, e.g. RFC-014 or CR-009")
    publish.add_argument("--type", required=True, dest="artifact_type",
                         help="an ontology object type: PRD, RFC, "
                              "ArchitectureDecisionRecord, ChangeRequest, Diagram, Review")
    publish.add_argument("--title", default="", help="optional human title for the node")
    publish.add_argument("--anchor", default="", help="optional anchor within the file")
    publish.add_argument("--json", action="store_true")
    publish.set_defaults(handler=_publish)


def _publish(args: argparse.Namespace) -> int:
    from weave.model.artifacts import PublishError, publish_artifact

    async def _run() -> Dict[str, Any]:
        rag, pool = await _local.product_engine(args)
        try:
            return await publish_artifact(
                rag, path=args.path, entity_id=args.artifact,
                entity_type=args.artifact_type, title=args.title,
                anchor=args.anchor,
                workspace=getattr(args, "workspace", "default"))
        finally:
            await pool.shutdown()

    try:
        report: Dict[str, Any] = asyncio.run(_run())
    except PublishError as e:
        # Not a traceback. The reader is a hook author or someone at a terminal,
        # and the message already says which file and what happened to it.
        raise SystemExit(str(e))

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if report["changed"]:
        print(f"published {report['artifact']} → {report['path']} "
              f"@ {report['locator']['rev'][:8]}")
    else:
        print(f"{report['artifact']} is already published at "
              f"{report['path']} @ {report['locator']['rev'][:8]} — nothing written")
    return 0
