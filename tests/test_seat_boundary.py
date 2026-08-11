"""The subscription seat boundary, end to end (A13).

A13 is the constraint the whole architecture was shaped around, and **no gate has
exercised it before now**. It says: every Claude Code client — human seat and dev
container alike — authenticates by subscription only. No API key, auth token or
base-URL override may reach a Claude Code process, and the `anthropic` SDK is not
a dependency. Server-side model use is the *only* place a model credential
exists.

The asymmetry is the part most likely to be "tidied" by someone helpful:
**`CLAUDE_CODE_OAUTH_TOKEN` is deliberately NOT scrubbed.** Scrubbing removes the
seat rather than protecting it — it is the opposite of metered auth. Adding it to
the scrub list would look like consistency and would break every dev container.

`test_no_sdk.py` already asserts the static half (no import, no manifest entry,
the scrub list's contents). What is asserted here is the **behaviour**: a
poisoned environment reaching a real preflight, and a container spec built from a
host that has every metered variable set.
"""

from __future__ import annotations

import pytest

from weave.team.worker import (
    SUBSCRIPTION_SCRUB_VARS,
    SubscriptionAuthError,
    preflight_subscription_auth,
    scrub_api_auth,
)

pytestmark = pytest.mark.offline

SEAT_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

#: What `claude auth status --json` says on a healthy subscription seat.
GOOD_STATUS = (
    '{"loggedIn": true, "apiProvider": "firstParty", "authMethod": "oauth", '
    '"subscriptionType": "max", "email": "dev@example.com"}'
)

#: A fully poisoned host: every way to put Claude Code on a metered path.
POISONED = {
    "ANTHROPIC_API_KEY": "sk-ant-should-never-reach-claude",
    "ANTHROPIC_AUTH_TOKEN": "also-metered",
    "ANTHROPIC_BASE_URL": "https://not-anthropic.example.com",
    "AWS_BEARER_TOKEN_BEDROCK": "bedrock",
    "AWS_ACCESS_KEY_ID": "AKIA...",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "ANTHROPIC_VERTEX_PROJECT_ID": "vertex-project",
    "GOOGLE_APPLICATION_CREDENTIALS": "/etc/gcp.json",
    SEAT_VAR: "the-seat-that-must-survive",
    "PATH": "/usr/bin",
}


# ── a poisoned environment never reaches Claude Code ─────────────────────────


def test_preflight_hands_claude_an_environment_with_no_metered_auth():
    """The end-to-end assertion A13 exists for.

    The env handed to `claude` is captured from the actual preflight rather than
    constructed by the test, so this fails if the scrub is ever skipped, reordered
    after the status probe, or applied to a copy that is then discarded.
    """
    seen: dict = {}

    def status(env):
        seen.update(env)
        return GOOD_STATUS

    handed = preflight_subscription_auth(env=dict(POISONED), status_fn=status)

    for var in SUBSCRIPTION_SCRUB_VARS:
        assert var not in handed, f"{var} reached the environment handed to claude"
        assert var not in seen, f"{var} reached the status probe"
    assert "sk-ant-should-never-reach-claude" not in repr(handed)


def test_the_seat_survives_the_scrub():
    """The asymmetry, asserted so nobody "fixes" it into consistency.

    Scrubbing `CLAUDE_CODE_OAUTH_TOKEN` would remove the seat rather than protect
    it — it is not metered auth, it *is* the subscription.
    """
    handed = preflight_subscription_auth(env=dict(POISONED), status_fn=lambda e: GOOD_STATUS)

    assert handed[SEAT_VAR] == "the-seat-that-must-survive"
    assert SEAT_VAR not in SUBSCRIPTION_SCRUB_VARS


def test_ordinary_variables_are_left_alone():
    """The scrub is a denylist of metered auth, not a sanitiser. A container still
    needs a PATH."""
    handed = preflight_subscription_auth(env=dict(POISONED), status_fn=lambda e: GOOD_STATUS)
    assert handed["PATH"] == "/usr/bin"


# ── an explicitly metered deployment is refused, not scrubbed ────────────────


@pytest.mark.parametrize("flag", ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"])
def test_an_explicit_metered_backend_refuses_to_start(flag):
    """These say *use the metered path*, and quietly unsetting them would start a
    worker whose operator believes it is on Bedrock. Refused instead."""
    with pytest.raises(SubscriptionAuthError) as exc:
        preflight_subscription_auth(env={flag: "1"}, status_fn=lambda e: GOOD_STATUS)
    assert flag in str(exc.value)


@pytest.mark.parametrize("status,why", [
    ('{"loggedIn": false}', "not logged in"),
    ('{"loggedIn": true, "apiProvider": "bedrock", "subscriptionType": "max"}',
     "a non-first-party provider"),
    ('{"loggedIn": true, "apiProvider": "firstParty", "authMethod": "api key", '
     '"subscriptionType": "max"}', "api-key auth"),
    ('{"loggedIn": true, "apiProvider": "firstParty", "authMethod": "oauth", '
     '"subscriptionType": "none"}', "no subscription"),
])
def test_a_seat_that_is_not_a_subscription_is_refused(status, why):
    """Every way the probe can say "this is not a subscription seat"."""
    with pytest.raises(SubscriptionAuthError):
        preflight_subscription_auth(env={"PATH": "/usr/bin"}, status_fn=lambda e: status)


def test_an_unreadable_status_is_refused_rather_than_assumed_good():
    """Silence is not confirmation. A preflight that passed on an unparseable
    answer would be a preflight in name only."""
    with pytest.raises(SubscriptionAuthError):
        preflight_subscription_auth(env={}, status_fn=lambda e: "command not found")


def test_a_healthy_seat_passes():
    """The check has to admit the good case, or it is just an outage."""
    handed = preflight_subscription_auth(env={"PATH": "/usr/bin", SEAT_VAR: "t"},
                                         status_fn=lambda e: GOOD_STATUS)
    assert handed[SEAT_VAR] == "t"


# ── the same boundary at the container edge ──────────────────────────────────


def test_scrub_is_idempotent_and_total():
    """Applied twice at different layers by design — the daemon allowlists, then
    scrubs again at the spec boundary — so it must be safe to repeat."""
    once = scrub_api_auth(dict(POISONED))
    twice = scrub_api_auth(once)
    assert once == twice
    assert not (set(twice) & set(SUBSCRIPTION_SCRUB_VARS))


def test_the_container_env_is_an_allowlist_not_the_hosts_environment():
    """A15/A13 at the container edge: a dev container gets named variables plus
    its seat, never whatever happened to be exported on the machine.

    Built from a fully poisoned host, so a passthrough entry that ever admitted a
    metered variable would surface here.
    """
    from weave.devhost.daemon import CONTAINER_ENV_PASSTHROUGH

    host_env = {**POISONED, "SOME_UNRELATED_SECRET": "should-not-travel"}
    container_env = {k: v for k, v in host_env.items() if k in CONTAINER_ENV_PASSTHROUGH}
    container_env[SEAT_VAR] = host_env[SEAT_VAR]
    container_env = scrub_api_auth(container_env)

    assert "SOME_UNRELATED_SECRET" not in container_env
    for var in SUBSCRIPTION_SCRUB_VARS:
        assert var not in container_env
    assert container_env[SEAT_VAR] == "the-seat-that-must-survive"


def test_no_metered_variable_is_in_the_passthrough_allowlist():
    """The allowlist is the first lock and the scrub is the second. This asserts
    the first — so a future passthrough entry cannot quietly reopen the boundary
    and rely on the scrub to catch it."""
    from weave.devhost.daemon import CONTAINER_ENV_PASSTHROUGH

    overlap = set(CONTAINER_ENV_PASSTHROUGH) & set(SUBSCRIPTION_SCRUB_VARS)
    assert not overlap, f"metered variables are passed through to containers: {overlap}"
