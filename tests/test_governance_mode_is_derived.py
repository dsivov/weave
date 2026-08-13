"""What governance is in force is **derived**, never stored (U17, A8).

dsivov changed Team vocabulary from Solo to Reviewed and signed it. It worked —
`/rbac` and `/lifecycle` both read back `name: "reviewed", version: 2`, with the
architect's approval gate enforced. **No screen said so.** The wizard's four
sections are all about *changing* governance; the "what happened" panel lives
only in the session that applied it, and the board's chip said `installed`
rather than which.

Same family as U10's silent save, one step worse: U10 was silent about an
**event**, so waiting fixed it. This is silent about **state** — wrong every
time anyone looks.

**The design constraint is the point of these tests.** A stored `current_mode`
would be a second source of truth, which A8 forbids: edit the ontology or rules
directly through Studio and the label goes on claiming Reviewed while the
runtime enforces something else. That is the wizard-writes-what-the-runtime-
does-not-read failure arriving from the other direction — so the mode is read
off the artifacts the runtime enforces, because those cannot disagree with
reality.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_NEXT = _ROOT / "weave-ui" / "src" / "features" / "next"
_IN_FORCE = _NEXT / "governance" / "InForceNow.tsx"

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code(path: pathlib.Path) -> str:
    return _COMMENTS.sub("", path.read_text(encoding="utf-8"))


# ── derived, not stored ──────────────────────────────────────────────────────


def test_the_mode_comes_from_the_signed_artifacts():
    """From `/rbac` and `/lifecycle` — the documents the runtime enforces."""
    code = _code(_IN_FORCE)
    assert "getRbac" in code and "getLifecycle" in code, (
        "the mode is no longer read from the artifacts that carry it"
    )


def test_nothing_stores_a_mode_label():
    """The constraint (A8).

    A `current_mode` in settings, localStorage or a server field would be a
    second source of truth, and the moment somebody edits governance through
    Studio it would start lying. There is nothing to keep in sync because there
    is nothing stored.
    """
    # Names that could only mean *governance* mode. A bare `setMode(` was the
    # first version and it flagged `ChunkInspector`'s chunk-display toggle —
    # stricter than the truth, which is how a guard gets switched off rather
    # than fixed.
    patterns = (
        r"currentMode", r"current_mode",
        r"governanceMode", r"governance_mode",
        r"setInForce\(", r"storedMode",
    )
    offenders = []
    for path in sorted(_NEXT.rglob("*.tsx")) + sorted(_NEXT.rglob("*.ts")):
        code = _code(path)
        for pattern in patterns:
            if re.search(pattern, code):
                offenders.append(f"{path.relative_to(_NEXT)}: {pattern}")
    assert not offenders, (
        "something stores a governance mode rather than deriving it (A8):\n  "
        + "\n  ".join(offenders)
    )


def test_a_disagreement_between_the_two_is_shown_not_smoothed():
    """RBAC and lifecycle are signed together by the wizard, so different names
    mean one of them was changed separately — which is exactly the state a
    stored label would have hidden, and therefore the one worth surfacing."""
    code = _code(_IN_FORCE)
    assert "disagreement" in code
    assert "role=\"alert\"" in code, (
        "a mismatch between RBAC and lifecycle is computed but not shown"
    )


# ── the answer is visible where the questions are asked ──────────────────────


def test_the_wizard_shows_what_is_in_force_before_offering_to_change_it():
    """The first question a returning user has is *what did I install last
    time*, and until now the only way to answer it was to curl the ledger."""
    code = _code(_NEXT / "pages" / "Wizard.tsx")
    assert "InForceNow" in code
    # The **render** site, not the first mention. The first version compared
    # `code.index("InForceNow")`, which is the import at the top of the file and
    # therefore always earlier than anything — so moving the panel below the
    # chooser did not fail it. A test that cannot fail is not a test.
    render = code.index("<InForceNow")
    assert render < code.index("templates.map"), (
        "the in-force panel renders after the chooser — a user picks before "
        "being told what they already have"
    )


def test_the_chooser_marks_the_installed_shape():
    """Re-picking should be informed rather than a guess."""
    code = _code(_NEXT / "pages" / "Wizard.tsx")
    assert "inForce.mode === t.id" in code, (
        "the chooser presents every shape as equally unchosen"
    )


def test_re_picking_a_different_shape_warns_that_it_replaces():
    """The diff shows the **target** state, not the delta from the current one —
    so *"you are removing the architect's approval gate"* is not on screen at the
    moment of choosing. The warning goes where the choice is made.
    """
    code = _code(_NEXT / "pages" / "Wizard.tsx")
    assert "Replaces" in code


def test_signing_refreshes_what_is_in_force():
    """Otherwise the panel whose whole job is saying *this is current* is stale
    exactly when it matters most."""
    code = _code(_NEXT / "pages" / "Wizard.tsx")
    assert "inForce.refresh()" in code


def test_the_board_names_the_mode_rather_than_just_installed():
    """`installed` is true of both Solo and Reviewed. Which one is the thing a
    person needs."""
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    assert "inForce.mode" in code, (
        "the board still reports only that governance exists, not which"
    )


def test_installing_governance_refreshes_the_chip():
    code = _code(_NEXT / "pages" / "WeaveBoard.tsx")
    install = code[code.index("weaveBootstrap("):]
    assert "inForce.refresh()" in install[:200]


# ── one answer, not two ──────────────────────────────────────────────────────


def test_both_screens_read_it_through_the_same_hook():
    """The wizard and the board must not each work the mode out. Two derivations
    of one fact drift, and this is a fact about what the runtime enforces."""
    for page in ("Wizard.tsx", "WeaveBoard.tsx"):
        code = _code(_NEXT / "pages" / page)
        assert "useInForce()" in code, f"{page} does not use the shared hook"
        assert "getRbac" not in code, (
            f"{page} reads /rbac directly instead of through useInForce — that is "
            "a second derivation of the same fact"
        )
