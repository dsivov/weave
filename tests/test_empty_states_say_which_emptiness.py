"""An empty answer says *which* emptiness it is (U5).

Two different things produce no nodes, and telling a user the wrong one is the
defect:

* **the workspace has none** — "no capabilities recorded yet";
* **your anchor matched nothing** — which is a fact about the query.

`ask_features` uses its anchor **directly as a graph seed** (`answers.py:178`,
``seeds = [feature]``), so it takes a *node id* and not a feature name. Type
"authentication" and you match nothing — and the page then reported that the
system contained no capabilities. That is true of the answer and false about the
world, which is exactly W17's shape: `Learnings` said *"no insights recorded
yet"* while the store held fourteen, and it was right, and it was useless.

So the test is about the **distinction**, not the wording. A page that renders
one empty message for both cases fails here however well that message reads.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FEATURES = _ROOT / "weave-ui" / "src" / "features" / "next" / "pages" / "Features.tsx"
_ANSWERS = _ROOT / "weave" / "model" / "answers.py"

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code() -> str:
    return _COMMENTS.sub("", _FEATURES.read_text(encoding="utf-8"))


# ── the premise, from the server ─────────────────────────────────────────────


def test_the_anchor_really_is_a_node_id():
    """Checked against `answers.py` rather than assumed.

    If `ask_features` ever resolved names to ids, this whole fix would be
    solving a problem that no longer exists — and the page would be telling
    users to enter something the server no longer wants.
    """
    tree = ast.parse(_ANSWERS.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "ask_features"
    )
    body = ast.get_source_segment(_ANSWERS.read_text(encoding="utf-8"), fn) or ""
    assert "seeds = [feature]" in body, (
        "ask_features no longer seeds the walk with the raw anchor — if it now "
        "resolves names, the Features page should stop asking for an id"
    )


# ── the distinction ──────────────────────────────────────────────────────────


def test_the_empty_state_depends_on_whether_an_anchor_was_given():
    """The whole of U5.

    One message for both cases is the defect regardless of how it is worded: it
    reports on the workspace when the truth is about the query.
    """
    code = _code()
    # **Every** empty state, not any of them. Both lists on this page are
    # anchored by the same field, so one that branches and one that does not is
    # still a screen that reports an empty workspace when the anchor missed —
    # and the `any()` version of this test passed exactly that.
    empties = re.findall(r"empty=(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\")", code, re.S)
    assert len(empties) >= 2, (
        f"expected an `empty` message per answer list, found {len(empties)}"
    )
    bare = [e for e in empties if "applied" not in e]
    assert not bare, (
        "an empty state does not branch on whether an anchor was applied, so an "
        f"anchor that matched nothing reports an empty workspace (U5): {bare}"
    )


def test_an_unmatched_anchor_says_so_and_names_it():
    """Naming the anchor back is what turns 'nothing here' into 'nothing under
    *this*', which is the difference between a dead end and a next step."""
    code = _code()
    assert "{applied}" in code or "${applied}" in code, (
        "the unmatched-anchor message does not quote the anchor, so the reader "
        "cannot tell which of the two emptinesses they are looking at"
    )


def test_the_unanchored_message_still_describes_the_workspace():
    """The other branch has to keep being right. Replacing both with a
    query-shaped message would swap the defect rather than fix it."""
    code = _code()
    assert "No capabilities recorded yet" in code


# ── the anchor is choosable, not guessable ───────────────────────────────────


def test_the_page_offers_the_ids_that_exist():
    """A field that silently requires a node id is unusable without a list of
    them — the user has no way to see one. The unanchored answer already
    contains every Feature node, so the page has the list and only had to show
    it."""
    code = _code()
    assert "datalist" in code, (
        "the anchor is a node id with nothing to choose from — a user must guess "
        "an identifier the page already knows"
    )
    assert "known" in code


def test_the_ids_come_from_the_unanchored_answer_only():
    """An anchored answer describes a subgraph, so harvesting ids from it would
    narrow the list every time someone used it — the field would forget what
    exists as soon as it was used."""
    code = _code()
    block = code[code.index("setFeatures(f); setChanges(c)"):]
    assert "if (!anchor)" in block[:400], (
        "the known-id list is refreshed from anchored answers too, so it shrinks "
        "to whatever the last query returned"
    )
