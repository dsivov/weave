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
variable the refusal mentions is overridable in the bundle.** If the message
grows a third suggestion, this covers it without anyone remembering to.

The literals that remain are deliberate and are asserted as such — they describe
the inside of the container, not a preference, and a test that demanded
everything be overridable would push someone to parameterise the port mapping
against itself.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from weave_core.graph.storage import QuadrupleUnsupported, assert_quadruple_supported

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


def _refusal_message() -> str:
    with pytest.raises(QuadrupleUnsupported) as excinfo:
        assert_quadruple_supported("PGVectorStorage", True)
    return str(excinfo.value)


def _overridable(name: str) -> bool:
    """True if compose lets the environment supply *name*."""
    text = _COMPOSE.read_text(encoding="utf-8")
    return bool(re.search(rf"^\s*{re.escape(name)}:\s*\"?\$\{{{re.escape(name)}:", text, re.M))


def _mentioned_in(message: str) -> set[str]:
    return set(re.findall(r"\bWEAVE_[A-Z_]+\b", message))


# ── the property ─────────────────────────────────────────────────────────────


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
        if not _overridable(name)
    ]
    assert not unreachable, (
        "the refusal suggests variables the bundle hardcodes, so following its "
        f"advice changes nothing: {', '.join(unreachable)}\n\n"
        "  Use ${NAME:-default} in deploy/compose.yml, or stop suggesting them."
    )


def test_both_exits_the_message_offers_are_reachable():
    """Named explicitly as well as swept, because these two are the ones an
    operator actually meets and a regression in either is the whole bug back."""
    assert _overridable("WEAVE_ENABLE_QUADRUPLE")
    assert _overridable("WEAVE_VECTOR_STORAGE")


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
