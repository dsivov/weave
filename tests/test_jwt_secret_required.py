"""The server refuses to start on the published signing secret (S1, A6, A14).

The M0 review found this pair, and the pair is what made it dangerous:

* **S1** — `WEAVE_TOKEN_SECRET` defaulted to a constant written in this
  repository. Anyone who has read the repository could mint a token carrying any
  role, and the server only *warned*.
* **H1** — the warning named `TOKEN_SECRET`, a variable D-024 had renamed. An
  operator who followed it exactly changed nothing and had every reason to
  believe they were done.

Before P1 the exposure was theoretical: no deployment, no users to impersonate.
With a persisted user store it is a complete RBAC bypass, because A6 derives the
principal from the token and a forged token *is* an authenticated identity.

A warning that can be ignored is not a control, so it is now a refusal.
"""

from __future__ import annotations

import pytest

from weave.server.auth import (
    DEFAULT_TOKEN_SECRET,
    INSECURE_OVERRIDE_VAR,
    InsecureSigningSecret,
    assert_signing_secret_is_safe,
)


@pytest.mark.offline
def test_the_default_secret_is_refused():
    with pytest.raises(InsecureSigningSecret) as excinfo:
        assert_signing_secret_is_safe(DEFAULT_TOKEN_SECRET, env={})
    assert "Refusing to start" in str(excinfo.value)


@pytest.mark.offline
def test_the_refusal_names_the_variable_that_actually_exists():
    """H1, pinned. The instruction has to name a variable something reads.

    The old message said `TOKEN_SECRET`. Setting that changes nothing, and the
    warning keeps firing into a log the operator has already actioned — so they
    believe the door is shut while it stands open.
    """
    with pytest.raises(InsecureSigningSecret) as excinfo:
        assert_signing_secret_is_safe(DEFAULT_TOKEN_SECRET, env={})
    message = str(excinfo.value)
    assert "WEAVE_TOKEN_SECRET" in message
    # and never the pre-D-024 name on its own
    for line in message.splitlines():
        for token in line.replace("=", " ").replace("'", " ").split():
            if token.strip(":,.") == "TOKEN_SECRET":
                pytest.fail(f"the refusal still names the old variable: {line!r}")


@pytest.mark.offline
def test_the_refusal_hands_over_a_usable_secret():
    """An error that says "set a real secret" and stops is a puzzle, not help."""
    with pytest.raises(InsecureSigningSecret) as excinfo:
        assert_signing_secret_is_safe(DEFAULT_TOKEN_SECRET, env={})
    message = str(excinfo.value)
    assert "export WEAVE_TOKEN_SECRET=" in message
    generated = message.split("export WEAVE_TOKEN_SECRET=")[1].split("\n")[0].strip("'\" ")
    assert len(generated) >= 32, "the offered secret is too short to be worth offering"


@pytest.mark.offline
def test_a_real_secret_is_accepted():
    assert_signing_secret_is_safe("a-genuinely-random-48-byte-value-goes-right-here", env={})


@pytest.mark.offline
@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_the_development_override_is_honoured(value):
    """Local development must stay one command, not a secret-generation ritual."""
    assert_signing_secret_is_safe(DEFAULT_TOKEN_SECRET, env={INSECURE_OVERRIDE_VAR: value})


@pytest.mark.offline
@pytest.mark.parametrize("value", ["", "false", "0", "no", "maybe"])
def test_the_override_must_be_stated_affirmatively(value):
    """Anything other than an explicit yes is a no. A variable that happens to
    exist — set empty by a compose file, say — must not open the door."""
    with pytest.raises(InsecureSigningSecret):
        assert_signing_secret_is_safe(DEFAULT_TOKEN_SECRET, env={INSECURE_OVERRIDE_VAR: value})


@pytest.mark.offline
def test_the_override_is_not_a_configuration_setting():
    """It has to be set on purpose, per process.

    If it were an ordinary config key it would land in a deployment template,
    get copied to production, and quietly disable the control there. The check
    reads the environment directly for exactly that reason.
    """
    import inspect

    from weave.server import config

    source = inspect.getsource(config)
    assert INSECURE_OVERRIDE_VAR not in source, (
        "the insecure-secret override has become a configuration setting; it must "
        "stay an environment-only, per-process decision"
    )


@pytest.mark.offline
def test_creating_the_app_refuses_the_default_secret(monkeypatch):
    """The check is wired into startup, not merely available."""
    import inspect

    from weave.server import app as app_module

    # The call moved into `assert_startup_preconditions` (W19) so the entry
    # points could run it *before the splash screen*. The property is unchanged
    # and is what this asserts: the secret is checked before anything is built.
    source = inspect.getsource(app_module.create_app)
    head = source[: source.find("webui_assets_exist")]
    assert "assert_startup_preconditions" in head, (
        "create_app must assert the startup preconditions before it builds anything"
    )
    assert "assert_signing_secret_is_safe" in inspect.getsource(
        app_module.assert_startup_preconditions
    ), "the signing-secret check is no longer among the startup preconditions"
