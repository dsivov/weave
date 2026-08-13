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


#: Controls whose `title` **names the control** rather than explaining why it is
#: disabled — "Refresh", "Delete". Those are legitimate and no regex separates
#: them from an explanation, so they are declared here rather than guessed at.
#: Offender unless annotated: the cost of a false positive is one line, and the
#: cost of a false negative is a control that explains itself to nobody.
TITLE_NAMES_THE_CONTROL = {
    # Generic components: `title` and `disabled` are **props**. The caller
    # decides both, so the rule applies where they are used, not here.
    "ObjectSettingsSection.tsx": ("title",),
    "SettingsPopover.tsx": ("title",),
    "TopToolbar.tsx": ("title",),
    "ZoomControls.tsx": ("title",),

    # Names the control ("Refresh"); disabled only while it is running, which
    # the spinner already says.
    "DocumentsNext.tsx": ("refreshTooltip",),

    # These two were the real finds when the sweep widened: their titles used to
    # *describe the action* ("Ask every running machine to run one more
    # developer") while the reason for disablement went unsaid. Retitled to name
    # the control, because in both cases the reason is already on screen and
    # adjacent — "no machines have registered" has its own empty state, and the
    # scale-down button sits next to the count showing 0.
    "WeaveProjectPanel.tsx": ("Scale up", "Scale down"),

    # Names the action, and the reason is on screen beside it: the canvas is
    # empty, which is what `nodesLength === 0` means and what the user is
    # looking at.
    "InspectorPanel.tsx": ("Auto-arrange",),
}


def test_no_disabled_control_explains_itself_through_a_tooltip():
    """The reach — widened to match what the docstring actually claims.

    **The first version checked `title={… ? …}` and read as though it checked
    more.** A *constant* explanatory title walked straight through:

        title={busy ? "" : "You cannot do this yet"}   → caught
        title="You cannot do this yet"                 → not caught

    Both are a disabled control whose only explanation is a tooltip, which is
    the thing this file says is not an explanation. The manager injected the
    second and it passed.

    That is the reach-versus-claim pattern for the fourth time in this project,
    and the reason to widen rather than narrow the docstring is that **the
    docstring is the part a future reader trusts**. A guard that reads stronger
    than it is will be relied on for the case it misses.

    So: any `title` on a control disabled by an expression is an offender unless
    the file declares it as naming the control.
    """
    offenders = []
    for path in sorted(_NEXT.rglob("*.tsx")):
        code = _code(path)
        allowed = TITLE_NAMES_THE_CONTROL.get(path.name, ())
        for match in re.finditer(r"<button\b", code):
            window = code[match.start(): match.start() + 500]
            window = window[: window.find("</button>") if "</button>" in window else len(window)]
            # `disabled` by an expression — a control that is *sometimes* off.
            # `disabled={false}` or no `disabled` at all is not this rule's business.
            disabled = re.search(r"disabled=\{([^}]*)\}", window)
            if not disabled or disabled.group(1).strip() in ("false", ""):
                continue
            title = re.search(r'title=(?:\{([^}]*)\}|"([^"]*)")', window)
            if not title:
                continue
            text = (title.group(1) or title.group(2) or "").strip()
            if any(name in text for name in allowed):
                continue
            offenders.append(f"{path.relative_to(_NEXT)}: title={text[:48]}…")

    assert not offenders, (
        "a control that is sometimes disabled explains itself through a tooltip:\n  "
        + "\n  ".join(offenders)
        + "\n\n  A tooltip is unreachable on touch and suppressed on some browsers, so "
        "it is not\n  an explanation. Use `<Blockers reasons={…} />` — the same list "
        "that disables the\n  control. If the title merely *names* the control, add it "
        "to TITLE_NAMES_THE_CONTROL."
    )


# ── U14: an affordance, not an instruction to curl ───────────────────────────


def test_the_board_offers_a_button_rather_than_a_curl_instruction():
    """U14.

    The board said *"Run `POST /weave/bootstrap` as a manager/architect"* — on a
    screen that already knew `installed: false` and could simply do it. **A10
    says a human role is Claude Code or the web UI**; neither of those is a curl
    prompt, so telling a person to issue an HTTP request is the product declining
    to be the product.

    Same rule as U2/U6/U7/U10 rather than a new one: the application knew the
    answer and did not put it where the person was looking.
    """
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    assert "weaveBootstrap(" in code, (
        "the board no longer offers to install governance itself"
    )
    assert "POST /weave/bootstrap</code>" not in code, (
        "the board still instructs the user to issue an HTTP request by hand"
    )


def test_the_bootstrap_outcome_reports_beside_its_own_button():
    """Its own action state, not the modal's.

    The endpoint is gated to supervisors, so a developer pressing this gets a
    403 — and that answer has to land at the button, which is the whole rule.
    Sharing the modal's state would render it inside a dialog that is not open.
    """
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    assert "bootstrapping = useAction()" in code
    assert "ActionMessages state={bootstrapping}" in code, (
        "the install button's outcome is not rendered next to it"
    )


def test_the_board_does_not_install_governance_by_itself():
    """Deliberately a button and **not** an automatic install on first load.

    Seeding governance everywhere would install RBAC everywhere, and W16 is that
    an RBAC-enabled workspace denies every MCP agent — `rbac_service.check(ws,
    None, …)` fails closed because MCP carries no role. The demo tenant has been
    usable by agents precisely because nothing was installed in it, so the fix
    for empty governance would become the cause of an empty fleet. Sequenced
    behind W16, not taken here.
    """
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    install = code[code.index("weaveBootstrap("):]
    assert "onClick" in code[: code.index("weaveBootstrap(")][-400:], (
        "bootstrap is not reached from a click — if it now runs on load, that "
        "installs RBAC into every new workspace and locks MCP agents out (W16)"
    )
    assert "useEffect" not in install[:200]
