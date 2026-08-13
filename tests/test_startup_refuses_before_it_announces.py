"""Nothing claims the server is starting before the checks that can refuse (W19).

The refusal worked. The container still crash-looped, and reading the log is the
whole finding:

```
line 18 : 📡 Server Configuration:          ← a banner announcing a configured server
line 71 : QuadrupleUnsupported: PGVector…   ← the refusal, 53 lines later
RestartCount: 12                            ← in under a minute
```

Two separate mistakes, neither of them in the check itself:

1. **The splash printed first.** A banner is a claim about *outcome*; it was
   being made before anything that could falsify it had run. The operator was
   told the server was configured and then that it could not start.
2. **`restart: unless-stopped` on a deterministic error.** Restarting cannot fix
   a bad signing secret or a backend with no tables. The policy turned a
   one-line answer into twelve banners, and the useful line scrolled away.

These tests pin the ordering and the policy, because both are the kind of thing
that drifts back: a splash moves up when someone wants earlier feedback, and a
restart policy gets copied from the service above it.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_COMPOSE = _ROOT / "deploy" / "compose.yml"
_COMPOSE_DEVHOST = _ROOT / "deploy" / "compose.devhost.yml"


# ── 1 · the checks run before the banner ─────────────────────────────────────


@pytest.mark.parametrize("module,entry", [
    ("weave.server.app", "main"),
    ("weave.server.gunicorn", "main"),
])
def test_preconditions_run_before_the_splash(module, entry):
    """Both entry points, because guarding one is W4's shape.

    `gunicorn.py` has its own `main()` and its own splash call; fixing only the
    uvicorn path would leave the deployment that actually ships behind a worker
    manager announcing itself before it checks anything.
    """
    import importlib

    source = inspect.getsource(getattr(importlib.import_module(module), entry))
    assert "refuse_readably" in source, (
        f"{module}.{entry} does not run the startup preconditions"
    )
    assert "display_splash_screen" in source, (
        f"{module}.{entry} no longer shows a splash — check this test, not the code"
    )
    assert source.index("refuse_readably") < source.index("display_splash_screen"), (
        f"{module}.{entry} announces a configured server before checking it can "
        "start one (W19)"
    )


def test_every_refusal_lives_in_the_one_precondition_function():
    """So a new check cannot be added below the splash by accident.

    Each of these refuses to start for a different reason; what they share is
    that they must all happen before anything is announced or constructed.
    """
    from weave.server.app import assert_startup_preconditions

    source = inspect.getsource(assert_startup_preconditions)
    for check in ("assert_signing_secret_is_safe",
                  "assert_quadruple_supported",
                  "assert_bus_matches_deployment"):
        assert check in source, f"{check} is not among the startup preconditions"


def test_create_app_still_asserts_them_too():
    """Idempotent on purpose: `get_application()` under gunicorn reaches the app
    without going through either `main()`, so the entry points cannot be the only
    place this happens."""
    from weave.server.app import create_app

    assert "assert_startup_preconditions" in inspect.getsource(create_app)


# ── 2 · a deterministic error is not restarted ───────────────────────────────


def _restart_policy(compose: pathlib.Path, service: str) -> str:
    """The `restart:` of one service, read without a YAML dependency."""
    text = compose.read_text(encoding="utf-8")
    start = re.search(rf"^  {re.escape(service)}:$", text, re.M)
    assert start, f"no service '{service}' in {compose.name}"
    rest = text[start.end():]
    end = re.search(r"^  \w[\w-]*:$", rest, re.M)
    block = rest[: end.start()] if end else rest
    found = re.search(r"^\s*restart:\s*(\S+)", block, re.M)
    return found.group(1) if found else ""


def test_the_server_is_not_restarted_forever():
    """The criterion: a configuration error must stay dead and readable.

    `unless-stopped` here reprinted the banner and the refusal twelve times in
    under a minute. The message was correct every time and unreachable by the
    end of it.
    """
    policy = _restart_policy(_COMPOSE, "server")
    assert policy != "unless-stopped", (
        "the server is back on `unless-stopped` — a deterministic refusal will "
        "loop until the explanation scrolls away (W19)"
    )
    assert policy.startswith("on-failure"), (
        f"expected an `on-failure` policy with a cap, found {policy!r}"
    )
    assert re.match(r"on-failure:\d+$", policy), (
        f"`{policy}` has no attempt cap — uncapped on-failure loops just as far"
    )


def test_the_databases_keep_restarting():
    """The distinction is the point, so it is asserted rather than described.

    A database that dies really can be worth restarting; a server that refuses
    its own configuration cannot. Levelling the two down would trade a real
    behaviour away for a tidier file.
    """
    assert _restart_policy(_COMPOSE, "postgres") == "unless-stopped"
    assert _restart_policy(_COMPOSE, "neo4j") == "unless-stopped"


def test_the_reason_is_in_the_file_not_only_in_the_review():
    """A bare `on-failure:3` beside two `unless-stopped` services reads as an
    inconsistency and gets 'fixed'. The comment is what stops that."""
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "W19" in text
    assert "restarting cannot fix" in text.lower() or "cannot fix them" in text.lower()


def test_the_dev_host_keeps_unless_stopped_and_says_why():
    """Decided the other way, deliberately (W19).

    The daemon already survives the server being away — it waits rather than
    exiting — so there is no crash-loop to cause, and its config errors are one
    line rather than a banner. And `on-failure` does not restart after a reboot,
    so a laptop that restarts overnight would silently leave the fleet.
    """
    assert _restart_policy(_COMPOSE_DEVHOST, "devhost") == "unless-stopped"
    text = _COMPOSE_DEVHOST.read_text(encoding="utf-8")
    assert "reboot" in text.lower(), (
        "the dev host's different policy needs its reason beside it, or the next "
        "reader levels it with the server's"
    )


# ── 3 · a refusal is presented as an answer, not a crash ─────────────────────


def test_a_refusal_exits_with_the_message_and_no_traceback():
    """The last third of W19, and it only shows up by running the thing.

    With the ordering fixed the banner was gone — and the operator's first six
    lines were still `Traceback (most recent call last)`, with the explanation
    at line 11. Someone who has set one variable wrongly needs the sentence, not
    ten frames of `runpy` above it.

    `SystemExit` prints its argument and exits non-zero with no traceback. The
    exception classes are untouched, so callers that want to catch
    `QuadrupleUnsupported` still can — only the presentation at the entry point
    changed.
    """
    from weave.server.app import refuse_readably

    source = inspect.getsource(refuse_readably)
    assert "raise SystemExit" in source, (
        "a refusal escapes as a traceback again — the message should be the "
        "first thing an operator reads, not the eleventh line"
    )
    assert "Weave will not start" in source


def test_the_refusal_still_carries_the_original_reason():
    """The wrapper must not swallow what it wraps. A `SystemExit` saying only
    "Weave will not start" would be worse than the traceback it replaced."""
    import argparse

    from weave.server.app import refuse_readably

    args = argparse.Namespace(
        token_secret="a-perfectly-fine-secret-that-is-long-enough-xxxx",
        vector_storage="PGVectorStorage", use_quadruple=True,
        event_bus="inprocess", workers=1,
    )
    with pytest.raises(SystemExit) as excinfo:
        refuse_readably(args)

    message = str(excinfo.value)
    assert "decisions" in message and "communities" in message
    assert "D-039" in message
