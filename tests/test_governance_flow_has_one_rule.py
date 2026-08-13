"""The sign-off rule is wired to the screen, not merely present (CR-001, A8).

`weave-ui/src/features/next/governance/__tests__/SignOff.test.ts` proves the
rule is **right**. This proves it is **used** — that the hook and the panel both
defer to the one predicate rather than each carrying their own idea of when a
change may be signed.

**Two claims, and proving only the first would prove the wrong half.** D-038 was
exactly a case of the right rule existing in one file (`routers/wizard.py`
derived its signer from the token) while the wrong one shipped in another
(`/studio/apply` took it from the body). A correct `canSign` that the button
ignored would be that defect again, in a project that has now met it four times.

This lives in the Python suite because it is the half that can actually be
**run here**: bun is not installed, and after D-036 nothing runs it
automatically. A source-level check is weaker than executing the component — it
cannot prove the button renders — but it is not weaker than a `bun test` nobody
has run, and it is the strongest thing available without a browser.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_SIGNOFF = (pathlib.Path(__file__).resolve().parent.parent
            / "weave-ui" / "src" / "features" / "next" / "governance" / "SignOff.tsx")

#: Comments describe the rule constantly; only code implements it.
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code() -> str:
    return _COMMENTS.sub("", _SIGNOFF.read_text(encoding="utf-8"))


def test_the_predicate_exists_and_is_exported():
    """Exported because the `bun test` imports it. A rule that cannot be reached
    by a test is a rule that will be tested by nobody."""
    code = _code()
    assert re.search(r"export function canSign\(", code)
    # `signOffBlockers` is the same rule as a list of reasons, so the button and
    # the sentence explaining it cannot drift apart (U6/U7).
    assert re.search(r"export function signOffBlockers\(", code)
    assert "signOffBlockers(" in code[code.index("export function canSign("):], (
        "canSign no longer derives from signOffBlockers — two implementations "
        "of one rule is the shape this file exists to prevent"
    )


def test_the_hook_defers_to_the_predicate():
    """`useSignOff.sign()` is the guard that actually holds — a disabled button
    is a suggestion, and a caller with a keyboard is not obliged to take it."""
    code = _code()
    hook = code[code.index("export function useSignOff("):]
    hook = hook[:hook.index("export function SignOffPanel(")]
    assert "canSign(" in hook, (
        "useSignOff no longer calls canSign — the guard that holds has grown its "
        "own copy of the rule"
    )


def test_the_panel_defers_to_the_same_predicate():
    """And the button agrees with it, rather than approximating it."""
    code = _code()
    panel = code[code.index("export function SignOffPanel("):]
    panel = panel[:panel.index("export function AppliedPanel(")]
    # `canSign` or `signOffBlockers` — they are one list and one rule; the panel
    # uses the blockers form because it renders the reasons as well as disabling
    # the button (U6/U7). What must not happen is a third opinion.
    assert "canSign(" in panel or "signOffBlockers(" in panel, (
        "SignOffPanel no longer defers to the shared rule — the button and the "
        "explanation can now disagree, which is how a change gets signed with no "
        "reason"
    )


def test_neither_reimplements_the_check_alongside_it():
    """The failure this file is really about: calling the predicate *and* keeping
    a hand-rolled `reason.trim()` beside it, so the two drift and the weaker one
    decides."""
    code = _code()
    predicate_body = code[code.index("export function canSign("):]
    predicate_body = predicate_body[:predicate_body.index("\n}")]

    elsewhere = code.replace(predicate_body, "")
    strays = re.findall(r"reason\.trim\(\)(?:\s*(?:&&|\|\||\?|===|!==|\.length))", elsewhere)
    assert not strays, (
        "the reason check appears outside `canSign`: "
        f"{strays}. One rule, one implementation — two is how the D-038 shape "
        "recurs."
    )


def test_the_screens_that_sign_go_through_the_shared_flow():
    """The reach. A governance screen that rendered its own sign button would
    bypass every assertion above."""
    ui = _SIGNOFF.parent.parent
    offenders = []
    for path in sorted(ui.rglob("*.tsx")):
        if path == _SIGNOFF:
            continue
        code = _COMMENTS.sub("", path.read_text(encoding="utf-8"))
        # A screen that applies a signed change must reach it through the shared
        # flow rather than calling an apply endpoint straight from a button.
        if re.search(r"\b(wizardApply|studioApply)\b", code) and "useSignOff" not in code:
            offenders.append(str(path.relative_to(ui)))

    assert not offenders, (
        "a screen applies a governance change without the shared sign-off flow:\n  "
        + "\n  ".join(offenders)
        + "\n\n  That is a second implementation of 'may this be signed', which "
        "R10 forbids and D-032/033/034 spent three phases removing server-side."
    )
