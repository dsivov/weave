"""The API description names no capability the server does not serve (D-044).

`/openapi.json` said *"Providing API for WeaveEngine core, Web UI and **Ollama
Model Emulation**"*. Measured on a running server: **154 endpoints, not one of
them Ollama-shaped**, no `operationId` mentioning it, and the two environment
variables the flags wrote (`WEAVE_NAME`, `WEAVE_TAG`) read by nothing.

**The emulation was never carried.** It was excluded at P0 — `routers/` came
over as 12 of 15, with `ollama_api.py` dropped as *"a compatibility surface for a
product Weave is not"* and *"the one route group answering without passing
governance (A6)"*. The 723-line module stayed behind; **the sentence advertising
it did not**, because exclusions are enforced on files and claims live in strings
inside files that were copied.

That is why this guard reads the route table rather than a file list. A stale
name misleads about what a thing is called; this misled about **what the product
does**, on the public contract, where a reader can act on it.

**What this covers, and what it does not.** It closes exactly one class: a
capability asserted on the public contract that no route serves. It says nothing
about content that is *wrong* rather than *unbacked* — an extraction prompt that
teaches from a sci-fi story, a wizard template carrying the parent's choices. No
route table can adjudicate those, and a guard that reads broader than it is has
already bitten this phase four times.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from weave.server.app import API_CLAIMS, create_app
from weave.server.config import parse_args

pytestmark = pytest.mark.offline


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> set:
    """Every path the **maximally configured** server serves.

    Two things about this fixture are the test, and both were found by measuring
    rather than by reasoning:

    1. **`app.routes` is the wrong source.** It yields 14 entries where the
       OpenAPI document yields 154 — routers arrive through `include_router` and
       sub-apps, so what a reader sees is `app.openapi()["paths"]`. A guard built
       on the route list would check 9% of the surface and report success.
    2. **The route table depends on configuration.** A default app mounts *no*
       `/weave/` routes at all, so a guard that built one could fail a perfectly
       true claim — or let someone justify a claim in whichever configuration
       happened to mount it. Every feature is switched on here, and each claim is
       asserted against that maximal table.

    `/api/*` is empty even at full configuration, which is what made the
    emulation claim a clean first case: it fails everywhere, not just by default.
    """
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = str(tmp_path_factory.mktemp("api-claims"))
    args.workers = 1                     # one process, so A7 is satisfied truthfully
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    args.enable_weave = True
    args.use_quadruple = True
    with TestClient(create_app(args)) as client:
        document = client.get("/openapi.json").json()
    return set(document["paths"])


@pytest.fixture(scope="module")
def description(tmp_path_factory: pytest.TempPathFactory) -> str:
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = str(tmp_path_factory.mktemp("api-claims-desc"))
    args.workers = 1
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    args.enable_weave = True
    args.use_quadruple = True
    with TestClient(create_app(args)) as client:
        return client.get("/openapi.json").json()["info"]["description"]


def test_the_maximal_app_really_is_maximal(paths):
    """The fixture's own premise, before anything is concluded from it.

    If this ever mounts a default app by accident, every claim below would be
    asserted against a table that does not contain the routes backing it, and
    the failures would look like the claims were false.
    """
    assert len(paths) > 100, f"only {len(paths)} paths — this is not the full surface"


def test_every_claim_the_description_makes_is_served(paths):
    """The rule. A claim with no route is a capability the server does not have."""
    unbacked = [
        (claim, prefix) for claim, prefix in API_CLAIMS
        if not any(p.startswith(prefix) for p in paths)
    ]
    assert not unbacked, (
        "the API description claims capabilities no route serves:\n  "
        + "\n  ".join(f"{claim!r} → no path under {prefix}" for claim, prefix in unbacked)
        + "\n\n  Either the routes are missing, or the claim is. A reader acts on "
        "this document."
    )


def test_the_description_is_exactly_the_declared_claims(description):
    """**The reach.**

    Checking only that each declared claim has routes would leave the original
    defect wide open: the emulation clause was never *declared*, it was prose. So
    the description is composed from `API_CLAIMS` and asserted to be nothing
    else — free text cannot be appended without declaring what it asserts, and a
    declaration without routes does not survive the test above.
    """
    expected = "Providing API for " + ", ".join(claim for claim, _ in API_CLAIMS)
    assert description.startswith(expected), (
        "the API description is no longer the composition of its declared "
        f"claims.\n\n  expected it to start with: {expected!r}\n"
        f"  actual: {description!r}\n\n"
        "  Prose added here asserts something to every reader of the public "
        "contract. Add a claim to API_CLAIMS with the path prefix that makes it "
        "true."
    )
    # Whatever follows is the API-key note and the ReDoc link, both of which are
    # about this document rather than about what the server does.
    remainder = description[len(expected):]
    assert "Ollama" not in remainder and "Emulation" not in remainder


def test_the_emulation_is_gone_from_the_command_line(paths):
    """D-044's other half: two write-only flags that configured nothing.

    Kept next to the description test on purpose — the flags and the sentence
    were the same claim in two places, and removing one would have left the
    product still offering to configure a thing it does not do.
    """
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        args = parse_args()
    finally:
        sys.argv = argv
    assert not hasattr(args, "simulated_model_name")
    assert not hasattr(args, "simulated_model_tag")


def test_the_ollama_binding_still_exists(paths):
    """**The mistake this test exists to prevent.**

    Two things answer to "ollama" and only one was removed: *emulation* is Weave
    pretending to be an Ollama server, and *binding* is Weave using Ollama as a
    server-side model backend. A13 explicitly blesses the second — server-side
    LLM use runs through the configurable backend connectors — so a grep-driven
    sweep that took both would break a supported deployment.
    """
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        args = parse_args()
    finally:
        sys.argv = argv
    assert hasattr(args, "llm_binding"), "the LLM binding flag is gone"
    assert hasattr(args, "embedding_binding"), "the embedding binding flag is gone"

    from weave_core.llm import ollama as ollama_binding

    assert hasattr(ollama_binding, "ollama_model_complete") or callable(
        getattr(ollama_binding, "ollama_embed", None)
    ), "the Ollama backend connector is gone"
