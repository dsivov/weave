"""`weave doctor` — tell an operator which of the seat problems they have (A13).

On a clean machine the question is "why is nothing happening", and the answers
look identical from outside: no seat, an expired seat, or a metered variable
exported in the shell that a worker will refuse to start alongside. `doctor`
separates them.

Two design points are asserted rather than assumed, because both are the kind of
helpfulness that would break the boundary:

1. **It reports, it does not repair.** A metered variable is named, not unset —
   an operator who exported `ANTHROPIC_API_KEY` believes it is being used, and
   silently removing it would make the bill the way they find out otherwise.
2. **The seat token is never a problem.** `CLAUDE_CODE_OAUTH_TOKEN` is the
   subscription, not metered auth, and reporting it as a fault is how someone
   ends up "fixing" the seat away.
"""

from __future__ import annotations

import pytest

from weave.cli.doctor import SEAT_TOKEN_VAR, diagnose

pytestmark = pytest.mark.offline

GOOD = ('{"loggedIn": true, "apiProvider": "firstParty", "authMethod": "oauth", '
        '"subscriptionType": "max", "email": "dev@example.com"}')
NOT_LOGGED_IN = '{"loggedIn": false}'
NO_SUBSCRIPTION = ('{"loggedIn": true, "apiProvider": "firstParty", '
                   '"authMethod": "oauth", "subscriptionType": "none"}')


@pytest.fixture(autouse=True)
def claude_on_path(monkeypatch):
    """Assume the CLI is installed unless a test says otherwise."""
    monkeypatch.setattr("weave.cli.doctor.shutil.which", lambda _n: "/usr/bin/claude")


# ── the three seat states ────────────────────────────────────────────────────


def test_a_healthy_subscription_reads_ok():
    report = diagnose(env={"PATH": "/usr/bin"}, status_fn=lambda e: GOOD)

    assert report["seat"] == "ok" and report["ok"] is True
    assert "max" in report["detail"]
    assert report["problems"] == []


def test_a_missing_login_says_what_to_run():
    report = diagnose(env={}, status_fn=lambda e: NOT_LOGGED_IN)

    assert report["seat"] == "missing"
    assert report["ok"] is False
    assert any("claude auth login" in a for a in report["advice"])


def test_a_login_without_a_subscription_is_distinguished_from_no_login():
    """Different problem, different fix. Telling someone to log in when they are
    already logged in is how a diagnostic loses their trust."""
    report = diagnose(env={}, status_fn=lambda e: NO_SUBSCRIPTION)

    assert report["seat"] == "expired"
    assert "seat-not-a-subscription" in report["problems"]
    assert not any("claude auth login" in a for a in report["advice"])


def test_an_unreadable_status_is_unknown_rather_than_ok():
    """Silence is not confirmation."""
    report = diagnose(env={}, status_fn=lambda e: "bash: claude: not found")

    assert report["seat"] == "unknown"
    assert report["ok"] is False


def test_claude_not_installed_is_reported_first():
    """Nothing else matters if the client is absent — every role is an ordinary
    Claude Code session (A10)."""
    import weave.cli.doctor as doctor_mod

    original = doctor_mod.shutil.which
    doctor_mod.shutil.which = lambda _n: None
    try:
        report = diagnose(env={}, status_fn=lambda e: GOOD)
    finally:
        doctor_mod.shutil.which = original

    assert report["claude_installed"] is False
    assert "claude-not-installed" in report["problems"]


# ── metered variables are reported, never repaired ───────────────────────────


def test_a_metered_variable_is_named_not_removed():
    env = {"ANTHROPIC_API_KEY": "sk-ant-x", "PATH": "/usr/bin"}

    report = diagnose(env=env, status_fn=lambda e: GOOD)

    assert report["metered_variables"] == ["ANTHROPIC_API_KEY"]
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x", (
        "doctor modified the environment it was asked to diagnose"
    )


def test_an_explicit_metered_backend_makes_the_machine_not_ok():
    """A credential lying around is a warning; `CLAUDE_CODE_USE_BEDROCK` is a
    decision, and a worker refuses to start on it."""
    report = diagnose(env={"CLAUDE_CODE_USE_BEDROCK": "1"}, status_fn=lambda e: GOOD)

    assert report["ok"] is False
    assert "explicit-metered-backend" in report["problems"]


def test_a_stray_credential_is_a_warning_not_a_refusal():
    """The seat is still fine; the shell is merely configured for something Weave
    does not use. Grading these the same would train people to ignore both."""
    report = diagnose(env={"ANTHROPIC_API_KEY": "x"}, status_fn=lambda e: GOOD)

    assert report["seat"] == "ok"
    assert report["ok"] is True
    assert "metered-variables-present" in report["problems"]


def test_the_status_probe_never_sees_metered_auth():
    """Even the diagnostic hands `claude` a scrubbed environment — a doctor that
    put the machine on a paid path while checking it would be absurd."""
    seen: dict = {}

    def status(env):
        seen.update(env)
        return GOOD

    diagnose(env={"ANTHROPIC_API_KEY": "sk-ant-x", "PATH": "/usr/bin"}, status_fn=status)

    assert "ANTHROPIC_API_KEY" not in seen


# ── the seat token is the seat ───────────────────────────────────────────────


def test_the_seat_token_is_reported_as_the_seat_not_as_a_problem():
    """A13's asymmetry. Reporting it as metered is how someone removes it."""
    report = diagnose(env={SEAT_TOKEN_VAR: "tok", "PATH": "/usr/bin"},
                      status_fn=lambda e: GOOD)

    assert report["seat_token_exported"] is True
    assert SEAT_TOKEN_VAR not in report["metered_variables"]
    assert report["ok"] is True


def test_no_seat_token_is_not_a_fault_either():
    """A machine using its own `claude` login exports nothing, and that is the
    ordinary case."""
    report = diagnose(env={"PATH": "/usr/bin"}, status_fn=lambda e: GOOD)

    assert report["seat_token_exported"] is False
    assert report["ok"] is True
