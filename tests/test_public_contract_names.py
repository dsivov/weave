"""The *generated* public contract carries no source-product name (A3).

`scripts/nameguard.sh` reads files. It cannot read what FastAPI builds at
runtime — and an operationId is not written down anywhere: FastAPI derives it
from the handler name plus the path. Two of the reasoning routes derive an id
whose join lands on the source product's name by accident, with no such string
in the source anywhere.

That id is part of a public contract: it names the operation in the OpenAPI
document, and every generated client and SDK takes its method name from it. So
it is exactly the kind of place A3 exists to protect, and exactly the kind the
static guard would never have found.

This test closes that blind spot for the whole document: paths, operation ids,
schema names, summaries and descriptions.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI

from weave.server.routers.reasoning import create_reasoning_routes

# Assembled, never written whole — this file is scanned by the guard too.
PATTERN = re.compile("light" + "rag" + "|" + "context" + "[ _-]?" + "graph", re.I)


class _FakeRag:
    """Enough of an engine for route registration; nothing is called."""

    rules_gate = None
    chunk_entity_relation_graph = None


def _spec() -> dict:
    app = FastAPI(title="Weave")
    app.include_router(create_reasoning_routes(_FakeRag()))
    return app.openapi()


@pytest.mark.offline
def test_no_operation_id_carries_a_source_product_name():
    offenders = []
    for path, methods in _spec()["paths"].items():
        for method, op in methods.items():
            op_id = op.get("operationId", "")
            if PATTERN.search(op_id):
                offenders.append(f"{method.upper()} {path} -> {op_id}")
    assert not offenders, (
        "generated operationId carries the source product's name:\n  "
        + "\n  ".join(offenders)
        + "\nPin it with operation_id=... on the route; every generated client "
          "takes its method name from this string."
    )


@pytest.mark.offline
def test_no_path_carries_a_source_product_name():
    offenders = [p for p in _spec()["paths"] if PATTERN.search(p)]
    assert not offenders, f"branded URL paths: {offenders}"


@pytest.mark.offline
def test_the_whole_generated_document_is_clean():
    """Paths, ids, schema names, summaries, descriptions — the lot."""
    document = json.dumps(_spec())
    hits = PATTERN.findall(document)
    assert not hits, (
        f"{len(hits)} occurrence(s) of a source product name in the generated "
        f"OpenAPI document: {sorted(set(h.lower() for h in hits))}"
    )
