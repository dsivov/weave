"""Every variable a refusal tells you to set must be settable (W20).

**The failure I set out to prevent, and then shipped.** When the server refuses
to start it names two exits: `WEAVE_ENABLE_QUADRUPLE=false`, or
`WEAVE_VECTOR_STORAGE=NanoVectorDBStorage`. I verified both **construct a
server** — and they did. In `deploy/compose.yml` both were **literals**:

```yaml
WEAVE_VECTOR_STORAGE: PGVectorStorage      # not ${VAR:-default}
WEAVE_ENABLE_QUADRUPLE: "true"
```

so exporting either changed nothing. The message addresses a bundle operator by
name — *"`deploy/compose.yml` ships this pair as its default"* — and for exactly
that reader both exits were unreachable. **Constructing is not the same as being
reachable**, which is the same distinction as *builds* versus *runs* at M6, one
layer in.

So this checks the property rather than the two names: **every `WEAVE_*`
variable a refusal mentions is overridable in the bundle.** If the message
grows a third suggestion, this covers it without anyone remembering to.

**Widened by P9, because the instance it was written for is gone.** W20 was
found on the D-039 quadruple refusal, and D-053 deleted that refusal — so a test
scoped to it would have been deleted with it, taking the property along. The
property was never about quadruple mode. It now sweeps **every** startup
refusal `assert_startup_preconditions` can raise, which is what it should have
done first: W20 is a claim about refusals, not about one of them.

**And widening it immediately found the same bug again.** The event-bus refusal
(A7, D-019) says *"export `WEAVE_EVENT_BUS=postgres`"* and the bundle has no
such variable — advice that changes nothing for the operator being addressed,
which is W20 word for word. It is not a live dead end *today* only because that
refusal cannot fire in the bundle: it needs more than one worker, and
`WEAVE_WORKERS` is pinned to 1. That premise is **asserted below** rather than
assumed, so raising the worker count turns this from a note into a failure.

The literals that remain are deliberate and are asserted as such — they describe
the inside of the container, not a preference, and a test that demanded
everything be overridable would push someone to parameterise the port mapping
against itself.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "compose.yml"

#: Fixed on purpose: they describe the inside of the container. The server must
#: bind all interfaces for the port mapping to reach it; 9800 is the container
#: side of that mapping; `postgres`/5432 is the service this file starts; and
#: WEAVE_WORKERS is 1 because this bundle ships the in-process bus (A7, D-019).
DELIBERATELY_FIXED = {
    "WEAVE_HOST", "WEAVE_PORT",
    "WEAVE_POSTGRES_HOST", "WEAVE_POSTGRES_PORT",
    "WEAVE_WORKERS",
}


#: A refusal whose advice the bundle cannot follow, and the reason it is not a
#: dead end. **The reason is a premise, not an excuse** — each entry names a
#: check that must still hold, and the test below runs it.
DECLARED_UNREACHABLE = {
    # The message itself says "Do not put that in a deployment template", so a
    # bundle that offered it would be contradicting the sentence that names it.
    "WEAVE_ALLOW_INSECURE_JWT_SECRET": "the dev escape hatch, deliberately absent from the bundle",
    # A7: this refusal fires only above one worker, and the bundle pins one.
    "WEAVE_EVENT_BUS": "the bus refusal cannot fire while WEAVE_WORKERS is pinned to 1",
}


def _refusal_messages() -> list[str]:
    """Every refusal `assert_startup_preconditions` can present to an operator.

    Driven by raising each one for real rather than by quoting its text: a
    message that changes its advice is followed automatically, and a check that
    stops refusing shows up as an empty list in the premise test below.
    """
    from weave.server.auth import DEFAULT_TOKEN_SECRET, assert_signing_secret_is_safe
    from weave.server.config import assert_bus_matches_deployment

    messages: list[str] = []
    for call, args, kwargs in (
        (assert_signing_secret_is_safe, (DEFAULT_TOKEN_SECRET,), {"env": {}}),
        (assert_bus_matches_deployment, ("inprocess", 4), {}),
    ):
        try:
            call(*args, **kwargs)
        except Exception as refusal:  # noqa: BLE001 - any refusal is in scope
            messages.append(str(refusal))
    return messages


def _refusal_message() -> str:
    return "\n".join(_refusal_messages())


def _overridable(name: str) -> bool:
    """True if compose lets the environment supply *name*."""
    text = _COMPOSE.read_text(encoding="utf-8")
    return bool(re.search(rf"^\s*{re.escape(name)}:\s*\"?\$\{{{re.escape(name)}:", text, re.M))


def _mentioned_in(message: str) -> set[str]:
    return set(re.findall(r"\bWEAVE_[A-Z_]+\b", message))


# ── the property ─────────────────────────────────────────────────────────────


def test_every_precondition_still_refuses():
    """Guards the guard, first half.

    `_refusal_messages` swallows a check that stopped raising, so without this
    the sweep would quietly measure fewer and fewer refusals until it measured
    none — passing the whole way down.
    """
    assert len(_refusal_messages()) == 2, (
        "a startup precondition stopped refusing, or a new one is not listed "
        "here — the sweep below only covers what this function raises"
    )


def test_the_refusal_names_at_least_one_variable():
    """Guards the guard: a message that stopped naming variables would make the
    sweep below pass over nothing."""
    assert _mentioned_in(_refusal_message()), (
        "the refusal suggests no environment variable — either it stopped "
        "offering an exit, or this test is checking the wrong message"
    )


def test_every_variable_the_refusal_suggests_is_overridable_in_the_bundle():
    """The whole of W20.

    The message speaks to a bundle operator by name. If it tells them to export
    something the bundle hardcodes, the advice is a dead end and the only way
    out is editing a file the message never mentions.
    """
    unreachable = [
        name for name in sorted(_mentioned_in(_refusal_message()))
        if not _overridable(name) and name not in DECLARED_UNREACHABLE
    ]
    assert not unreachable, (
        "the refusal suggests variables the bundle hardcodes, so following its "
        f"advice changes nothing: {', '.join(unreachable)}\n\n"
        "  Use ${NAME:-default} in deploy/compose.yml, or stop suggesting them."
    )


def test_the_declared_exceptions_still_have_their_reason():
    """**The premise behind each declared exception, checked.**

    `WEAVE_EVENT_BUS` is exempt only because the refusal that suggests it cannot
    fire in the bundle — it needs more than one worker, and the bundle pins one.
    If `WEAVE_WORKERS` ever becomes overridable, that refusal becomes reachable
    with advice the bundle still cannot take, and this must fail rather than
    keep honouring an exemption whose reason has expired.
    """
    assert not _overridable("WEAVE_WORKERS"), (
        "WEAVE_WORKERS became overridable, so the event-bus refusal can now fire "
        "in the bundle — and WEAVE_EVENT_BUS is still not settable there. Either "
        "add ${WEAVE_EVENT_BUS:-inprocess} to deploy/compose.yml or drop the "
        "exemption from DECLARED_UNREACHABLE."
    )
    for name in DECLARED_UNREACHABLE:
        assert name in _mentioned_in(_refusal_message()), (
            f"{name} is declared unreachable but no refusal mentions it any more "
            "— delete the entry rather than carrying a dead exemption"
        )


def test_the_storage_choice_is_overridable_as_a_set():
    """Switching one vector store while the other three stay pinned to
    PostgreSQL is a half-configuration nobody asked for."""
    for name in ("WEAVE_KV_STORAGE", "WEAVE_VECTOR_STORAGE",
                 "WEAVE_GRAPH_STORAGE", "WEAVE_DOC_STATUS_STORAGE"):
        assert _overridable(name), f"{name} is hardcoded in the bundle"


# ── what stays fixed, and why ────────────────────────────────────────────────


def test_the_container_internals_stay_fixed():
    """The opposite failure: parameterising everything.

    These describe the inside of the container rather than a preference.
    Overriding `WEAVE_PORT` here would break the published mapping instead of
    moving it, and `WEAVE_WORKERS` is 1 because this bundle ships the in-process
    bus — raising it silently breaks SSE fan-out (A7, D-019).
    """
    text = _COMPOSE.read_text(encoding="utf-8")
    for name in DELIBERATELY_FIXED:
        assert re.search(rf"^\s*{name}:", text, re.M), f"{name} vanished from the bundle"
        assert not _overridable(name), (
            f"{name} became overridable — it describes the inside of the "
            "container, and the reason is in the file beside it"
        )


def test_the_fixed_ones_say_why_in_the_file():
    """A literal beside seven `${VAR:-default}`s reads as an oversight and gets
    'fixed' by the next reader. The comment is what stops that."""
    text = _COMPOSE.read_text(encoding="utf-8")
    # Matched on a phrase no emphasis marker splits — the first version looked
    # for "inside of the container" and the comment reads "the *inside* of the
    # container", so the test failed on its own formatting.
    assert "not a preference" in text
    assert "W20" in text
