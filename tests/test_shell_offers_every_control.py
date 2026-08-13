"""The new shell offers every control the old header owned (U11 · U12 · U13).

**This test exists because of how M7 passed.** The gate recorded *"16/16 views
reachable"*, derived by comparing `ViewId` sets across commits — a real check
that answered a narrow question. Nobody derived the **control** set, so when
`App.tsx` made `AppShell` the whole app in `next` mode and stopped rendering
`SiteHeader`, **logout and the display of who you are left without a trace**.

Views is not chrome. A user can reach all sixteen screens and still be unable to
sign out — and without logout a token cannot be re-minted, so a role changed in
Admin ▸ Users saves, stays out of force, and leaves the owner unable to
configure their own installation (U1). Three missing controls composed into that.

So the set is **derived from `SiteHeader.tsx`** rather than listed here. If the
old header ever grows a control, this notices; if the shell is rewritten again,
the same check runs against whatever replaces it. Same instinct as reading the
variable names out of the refusal message in `test_refusal_advice_is_reachable`:
assert the property, not today's three names.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_UI = pathlib.Path(__file__).resolve().parent.parent / "weave-ui" / "src"
_HEADER = _UI / "features" / "SiteHeader.tsx"
_SHELL = _UI / "features" / "next" / "AppShell.tsx"

#: What each control looks like in source, on either surface. Matching on
#: behaviour rather than markup: the shell is a sidebar and the header was a
#: top bar, so the *elements* differ and the capability must not.
CONTROLS = {
    # **Wired, not merely defined.** The first version of this grepped the whole
    # file, so a `signOut` function that nothing rendered satisfied it — the
    # negative control caught that: reverting the shell to its broken footer left
    # the handler in place and the test passed. A control defined and not
    # rendered is exactly the "described, not offered" case this file is about,
    # so these match the *binding*.
    "logout": (r"onClick=\{\s*(signOut|handleLogout|logout)\b", "sign out of the session"),
    # `{username ??` / `{username}` — a JSX expression, not the destructuring
    # `const { username, role } = useAuthStore()`. The permissive version matched
    # the destructure and passed against a shell that rendered nothing.
    "identity": (r"\{username\s*(\?\?|\})", "show who is signed in"),
}


def _code(path: pathlib.Path) -> str:
    """Source with comments stripped — a control described is not a control
    offered, and this file is about the difference."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


# ── the derivation ───────────────────────────────────────────────────────────


def test_the_old_header_still_exists_to_derive_from():
    """Guards the guard. If `SiteHeader.tsx` is deleted, every check below would
    pass over an empty set — the vacuous pass this project has met as W5, W13
    and the preset-derived fixture."""
    assert _HEADER.exists(), (
        "SiteHeader.tsx is gone, so the control set cannot be derived from it. "
        "Replace this file's source of truth deliberately rather than letting "
        "the checks below silently assert nothing."
    )


@pytest.mark.parametrize("control", sorted(CONTROLS))
def test_the_old_header_really_owned_this_control(control):
    """The premise, checked. A control the header never had would make the
    corresponding shell assertion meaningless."""
    pattern, what = CONTROLS[control]
    header = _code(_HEADER)
    # The header binds logout to a `Button onClick`; identity appears in its
    # tooltip. Matched loosely here because the *premise* only needs the control
    # to have existed — the strict binding check is on the shell below.
    loose = {"logout": r"onClick=\{handleLogout\}", "identity": r"\busername\b"}[control]
    assert re.search(loose, header), (
        f"SiteHeader.tsx does not appear to {what} — the control set this test "
        "derives from has changed shape"
    )


# ── the property ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("control", sorted(CONTROLS))
def test_the_shell_offers_the_control_too(control):
    """The whole point.

    `AppShell` is the entire application in `next` mode. Anything the classic
    header owned and the shell does not is simply absent for every user.
    """
    pattern, what = CONTROLS[control]
    assert re.search(pattern, _code(_SHELL)), (
        f"AppShell.tsx offers no way to {what}. `SiteHeader` is not rendered in "
        "`next` mode, so a control only it has is a control nobody has (U11/U12)."
    )


def test_the_shell_can_actually_end_the_session():
    """Not merely a link to the login page.

    Navigating to `/login` while a valid token sits in localStorage does not sign
    anyone out — it is a redirect, and the next request still carries the old
    identity. Ending the session means clearing the token *and* navigating.
    """
    code = _code(_SHELL)
    assert "logout()" in code, (
        "the shell navigates to login without clearing the token — the session "
        "survives, and the token still carries the old role (U1)"
    )
    assert "navigateToLogin" in code, (
        "the shell clears the token without navigating, leaving the user on a "
        "screen that can no longer talk to the server"
    )


def test_the_role_shown_is_the_one_the_server_enforces():
    """`role` from the token, not from the user record.

    They differ exactly when it matters: a role changed in Admin ▸ Users is
    saved and not yet in force, because the server reads the role from the token
    (D5). Showing the record's role would hide the very state that makes U1
    baffling — the change appears applied and nothing behaves differently.
    """
    assert "role" in _code(_SHELL)
    store = _code(_UI / "stores" / "state.ts")
    assert re.search(r"getRoleFromToken", store), (
        "the auth store no longer derives the role from the token"
    )


# ── the parent's initials are gone ───────────────────────────────────────────


def test_the_avatar_is_not_hardcoded():
    """U13. `CG` was the parent product's initials, shown to every user as their
    own — a rebrand the name-guard cannot catch, because it checks spellings and
    this is an abbreviation."""
    code = _code(_SHELL)
    assert not re.search(r'className="avatar"[^>]*>\s*[A-Z]{2}\s*<', code), (
        "the avatar still renders hardcoded initials instead of the signed-in "
        "user's (U13)"
    )
    assert "initials" in code, "the avatar shows nothing derived from the user"


def test_initials_come_from_the_username():
    """Asserted in the shell rather than trusted: an avatar computed from
    something else would satisfy the check above and still show the wrong
    person."""
    code = _code(_SHELL)
    block = code[code.index("const initials"):]
    assert "username" in block[:400]
