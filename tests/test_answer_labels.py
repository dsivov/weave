"""Every content field an answer carries can become a node's name (U3).

**Two lists, written independently, overlapping on one word.**

* `answers.py` emitted content as `title, status, summary, verdict, statement,
  sha, reviewer, confidence, text, asked_by, url, path`.
* `AnswerView.tsx` named a node from `title, name, entity_name, id`.

They intersect on `title`. So every node whose content lives in `statement`,
`summary` or `text` rendered as a raw id — `insight:T-P1-USERS:0` — with good
text sitting unread in the payload. Insights and Reviews, which is exactly the
Learnings screen.

Features and Changes looked identical in the symptom and were **fine**: their
ids *are* their names. That is why it read as cosmetic.

The fix is not a longer list in the renderer — that misses the next field the
same way. The server assembles the whitelist, so the server says which field is
the name, once, and MCP gets it too (A9).

**These tests exist so the two lists cannot drift again.** Every content field is
either reachable by the label chain or declared as not being a name — offender
unless annotated.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from weave.model.answers import CONTENT_FIELDS, LABEL_FIELDS, _node_view

pytestmark = pytest.mark.offline

_VIEW = (pathlib.Path(__file__).resolve().parent.parent / "weave-ui" / "src"
         / "features" / "next" / "governance" / "AnswerView.tsx")

#: Content that is real but is **not** a name — a status, a hash, a number, a
#: path. Declared rather than inferred: no rule separates "short string that
#: names a thing" from "short string that describes one", and guessing would put
#: `pass` or `0.82` in a heading.
NOT_A_NAME = {
    "status",      # 'review', 'approved' — a state, and every node has one
    "verdict",     # 'pass' / 'fail'
    "sha",         # a commit hash
    "reviewer",    # who, not what
    "confidence",  # a number
    "asked_by",    # who
    "url",         # the locator surfaces this properly
    "path",        # likewise
}


# ── the two lists cannot drift ───────────────────────────────────────────────


def test_every_content_field_is_a_name_or_declared_not_to_be():
    """The rule. A new content field is an offender until someone decides which
    it is — which is the decision that was never made for `statement`."""
    unaccounted = [
        f for f in CONTENT_FIELDS
        if f not in LABEL_FIELDS and f not in NOT_A_NAME
    ]
    assert not unaccounted, (
        "these fields carry content but can never become a node's name: "
        f"{unaccounted}\n\n  Add each to LABEL_FIELDS in weave/model/answers.py, "
        "or to NOT_A_NAME here with the reason."
    )


def test_the_declarations_do_not_overlap():
    """A field in both lists means someone edited one and not the other."""
    assert not (set(LABEL_FIELDS) & NOT_A_NAME)


def test_every_label_field_is_actually_carried():
    """A name chosen from a field the answer never emits would silently fall
    through to the id — the original defect, from the other side."""
    missing = [f for f in LABEL_FIELDS if f not in CONTENT_FIELDS]
    assert not missing, f"LABEL_FIELDS names fields the answer does not carry: {missing}"


# ── the label itself ─────────────────────────────────────────────────────────


def test_an_insight_is_named_by_its_statement():
    """The reported case, exactly."""
    view = _node_view("insight:T-P1-USERS:0", {
        "entity_type": "Insight",
        "statement": "Run the gate by hand on a live server, not only in the suite.",
    })
    assert view["label"].startswith("Run the gate by hand")
    assert view["id"] == "insight:T-P1-USERS:0"


def test_a_feature_is_named_by_its_id_and_that_is_correct():
    """Features looked broken and were not: the id is the human-readable name,
    so falling back to it is the right answer rather than a failure."""
    view = _node_view("Feature P0 — Fork and rebrand", {"entity_type": "Feature"})
    assert view["label"] == "Feature P0 — Fork and rebrand"


def test_title_wins_over_a_longer_body():
    """Order matters: a node with both should be named by the specific field, not
    the first paragraph of its body."""
    view = _node_view("t-1", {
        "entity_type": "Task", "title": "Add the session block",
        "summary": "A much longer description that would make a poor heading.",
    })
    assert view["label"] == "Add the session block"


def test_a_node_with_no_content_still_gets_a_name():
    """`label` is never empty. A renderer that has to handle a missing label is a
    renderer with its own fallback, which is what this replaced."""
    assert _node_view("bare:1", {"entity_type": "Thing"})["label"] == "bare:1"


def test_a_status_never_becomes_the_name():
    """The failure this guards: `status` is present on nearly every node, so
    admitting it to the chain would rename half the graph to 'review'."""
    view = _node_view("t-2", {"entity_type": "Task", "status": "review"})
    assert view["label"] == "t-2"


# ── the renderer stops guessing ──────────────────────────────────────────────


def test_the_renderer_reads_the_label_and_keeps_no_list_of_its_own():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", _VIEW.read_text(encoding="utf-8"), flags=re.S)
    assert "node.label" in code, "AnswerView no longer reads the server's label"
    assert "entity_name" not in code, (
        "AnswerView has grown its own list of name fields again — that is the "
        "second implementation this whole change removed"
    )


def test_the_id_stays_reachable_in_the_ui():
    """`/ask/why` anchors on the id, so replacing it with a label everywhere
    would close the path from a learning to its decision record."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", _VIEW.read_text(encoding="utf-8"), flags=re.S)
    assert "node.id" in code
