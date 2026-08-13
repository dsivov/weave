"""A control that will not act says why, in place (U2 · U6 · U7 · U10).

The rule, in full: *a control that will not act says why, in place, before it is
clicked; an action that fails says so where the click happened; an action that
succeeds says that too.*

Four sites broke it four ways, and the shape they share is the reason this is
one change: **the application knew the answer and did not put it where the
person was looking.**

* **U2** — `WeaveBoard`'s Approve sits in a `Modal`; `act()` rendered its error
  on the page *behind* it. The 403 was correct, rendered, and invisible.
* **U6 · U7** — `SignOff` disabled its button correctly and explained only via a
  `title` tooltip **on a disabled control**, which touch never shows.
* **U10** — `AdminUsers` hid a password rule behind the same pattern, and saved
  a role change in silence.

The manager's ask was that the rule survive contact with the fourth site rather
than being three fixes and a special case, so these tests assert it **per site**
and assert the shared primitives exist to carry it. A tooltip sweep runs over
the whole `next/` tree, because the next disabled control is the one nobody has
written yet.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_NEXT = (pathlib.Path(__file__).resolve().parent.parent
         / "weave-ui" / "src" / "features" / "next")
_FEEDBACK = _NEXT / "governance" / "ActionFeedback.tsx"

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code(path: pathlib.Path) -> str:
    return _COMMENTS.sub("", path.read_text(encoding="utf-8"))


# ── the shared primitives exist ──────────────────────────────────────────────


def test_the_rule_has_one_implementation():
    """Four sites, one treatment. Four local fixes would drift immediately, and
    the fourth would be the special case that proves the rule was never one."""
    code = _code(_FEEDBACK)
    for name in ("useAction", "ActionMessages", "Blockers"):
        assert f"export function {name}" in code, f"{name} is missing"


def test_success_is_reportable_not_only_failure():
    """Silence on success is its own defect (U10): the role change that started
    all of this saved correctly and told nobody."""
    code = _code(_FEEDBACK)
    assert "succeeded" in code
    assert "setNotice" in code


# ── per site ─────────────────────────────────────────────────────────────────


def test_the_board_reports_inside_the_modal():
    """U2. The click and its answer must share a container."""
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    assert "ActionMessages" in code, (
        "WeaveBoard reports governed-action outcomes at the page level again — "
        "behind the modal the click happened in"
    )
    assert "useAction()" in code


def test_the_sign_off_panel_shows_its_blockers():
    """U6 · U7. Not a `title` on a disabled button."""
    code = _code(_NEXT / "governance" / "SignOff.tsx")
    assert "signOffBlockers(" in code
    assert "<Blockers" in code, (
        "the sign-off panel disables its button without saying why in place"
    )


def test_the_create_form_states_its_rules():
    """U10's other half — 'cannot add users' was unstated preconditions, which
    is the same rule as the disabled sign button rather than its own bug."""
    code = _code(_NEXT / "pages" / "AdminUsers.tsx")
    assert "createBlockers" in code
    assert "<Blockers" in code, (
        "the create button is disabled with the password rule stated nowhere"
    )
    assert "disabled={createBlockers.length > 0}" in code, (
        "the button and its explanation are computed separately — they will drift"
    )


def test_the_role_change_says_when_it_takes_effect():
    """D-040's acceptance item, and the sentence whose absence produced U1."""
    code = _code(_NEXT / "pages" / "AdminUsers.tsx")
    assert "next time they sign in" in code, (
        "a role change no longer says it takes effect at the next sign-in — the "
        "absence of that sentence is what made U1 a dead end"
    )


# ── the class, over the whole tree ───────────────────────────────────────────


def test_no_disabled_control_explains_itself_only_through_a_tooltip():
    """The reach.

    A `title` on a disabled element is unreachable on touch and suppressed by
    some browsers — so it is not an explanation, it is a hope. The next disabled
    control is the one nobody has written yet, which is why this sweeps rather
    than listing the two we fixed.
    """
    # **Not a tag regex.** The first version matched `<button\b[^>]*?>`, which
    # stops at the first `>` — and `onClick={() => …}` contains one, so it read
    # a fragment of nearly every real button and the negative control did not
    # fire. A window after the opening tag is cruder and actually works.
    offenders = []
    for path in sorted(_NEXT.rglob("*.tsx")):
        code = _code(path)
        for match in re.finditer(r"<button\b", code):
            window = code[match.start(): match.start() + 500]
            window = window[: window.find("</button>") if "</button>" in window else len(window)]
            if "disabled={" not in window:
                continue
            # A constant title on any control is fine; the defect is a
            # *conditional* title standing in for why the control is disabled.
            if re.search(r"title=\{[^}]*\?", window):
                offenders.append(
                    f"{path.relative_to(_NEXT)}: {' '.join(window.split())[:80]}…"
                )

    assert not offenders, (
        "a disabled control explains itself only through a tooltip:\n  "
        + "\n  ".join(offenders)
        + "\n\n  Use `<Blockers reasons={…} />` — the same list that disables it."
    )
