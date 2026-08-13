"""The UI answers the four questions the way an agent does — or not at all (A9).

**This test replaced one that could not do what its name claimed.** CR-001
originally specified `test_ask_ui_parity.py`: *"the UI's node set equals the
API's"*. A Python test cannot observe the UI's node set, and the reachable
alternative — REST/MCP parity — is already covered by
`test_mcp_rest_parity.py`. **A test named for a gate criterion while asserting
something easy is worse than no test, because it retires the question**: the
criterion looks covered and nobody looks again.

So this asserts what *is* checkable and what A9 actually requires: **there is no
second way for the UI to answer these questions.** One handler serves REST and
MCP; a screen that assembled its own answer from the graph would be a third
implementation with no obligation to agree with either, and it would drift
silently because nothing compares them.

Three properties, none of them free:

1. the client's question table **matches the server's**, anchor rules included;
2. `/ask` requests are built in exactly one place;
3. the screens that render answers get them only from that place.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UI = _ROOT / "weave-ui" / "src"
_CLIENT = _UI / "api" / "weave.ts"
_ASK_ROUTER = _ROOT / "weave" / "server" / "routers" / "ask.py"


def _server_anchors() -> dict[str, tuple[str, bool]]:
    """`ANCHORS` from `routers/ask.py`, read rather than duplicated."""
    tree = ast.parse(_ASK_ROUTER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "ANCHORS" for t in node.targets)):
            return {
                ast.literal_eval(k): tuple(ast.literal_eval(v))
                for k, v in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("ANCHORS not found in routers/ask.py")


def _client_anchors() -> dict[str, tuple[str, bool]]:
    """`ASK_ANCHOR` from the TypeScript client, parsed out of the source."""
    source = _CLIENT.read_text(encoding="utf-8")
    block = re.search(
        r"export const ASK_ANCHOR[^=]*=\s*\{(.*?)\n\}", source, re.S)
    assert block, "ASK_ANCHOR not found in api/weave.ts"
    found = {}
    for name, param, required in re.findall(
        r"(\w+):\s*\{\s*param:\s*'([^']+)'\s*,\s*required:\s*(true|false)\s*\}",
        block.group(1),
    ):
        found[name] = (param, required == "true")
    return found


# ── 1 · the two tables agree ─────────────────────────────────────────────────


def test_the_ui_asks_exactly_the_questions_the_server_answers():
    """Not a subset and not a superset.

    A question the UI does not know is a capability with no surface — which is
    the entire complaint CR-001 was written about, where four endpoints had
    existed since P2 with no UI at all. A question the UI invents is a 404 the
    user meets instead of an answer.
    """
    assert set(_client_anchors()) == set(_server_anchors()), (
        f"client asks {sorted(_client_anchors())}, "
        f"server answers {sorted(_server_anchors())}"
    )


def test_the_anchor_rules_match_including_which_one_is_required():
    """`why` is the only anchor-required question, and the UI's behaviour turns
    on that: it is the reason `why` has no menu entry and is anchored on a node
    the user clicked instead. If the server ever made another question required,
    that page would silently start 400-ing."""
    assert _client_anchors() == _server_anchors()


# ── 2 · one place builds the request ─────────────────────────────────────────


#: A request being *made*, not the path being mentioned.
#:
#: The first version of this matched any quoted `/ask/`, and flagged four
#: innocent lines: three doc comments describing the endpoints and one JSX label
#: reading *"/ask/why — needs an anchor"*. **The test was stricter than the
#: truth** — the failure mode that gets a guard weakened rather than fixed, and
#: the same one that bit `test_cli_covers_docs.py` over a placeholder. Comments
#: are stripped and the path must sit inside a call.
_ASK_CALL = re.compile(r"""(?:axiosInstance|fetch|axios)\b[^\n]*["'`]/ask/""")
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def test_only_the_api_client_constructs_an_ask_request():
    offenders = []
    for path in sorted(_UI.rglob("*.ts")) + sorted(_UI.rglob("*.tsx")):
        if path == _CLIENT:
            continue
        source = _COMMENTS.sub("", path.read_text(encoding="utf-8"))
        for i, line in enumerate(source.splitlines(), 1):
            if _ASK_CALL.search(line):
                offenders.append(f"{path.relative_to(_UI)}:~{i}")

    assert not offenders, (
        "a component builds its own /ask request instead of using `ask()`:\n  "
        + "\n  ".join(offenders)
        + "\n\n  One call site is what keeps the UI's answer the same answer MCP "
        "gets (A9)."
    )


def test_the_client_targets_the_ask_endpoints_and_not_a_lookalike():
    """The request really is `/ask/<question>` — a client that quietly pointed at
    a different path would satisfy every other test here."""
    source = _CLIENT.read_text(encoding="utf-8")
    assert re.search(r"axiosInstance\.get\(`/ask/\$\{question\}`", source), (
        "`ask()` no longer requests /ask/${question}"
    )


# ── 3 · the screens have no other source ─────────────────────────────────────


def _modules_rendering_answers() -> list[pathlib.Path]:
    return [
        p for p in sorted(_UI.rglob("*.tsx"))
        if "AnswerList" in p.read_text(encoding="utf-8")
        and p.name != "AnswerView.tsx"
    ]


def test_screens_that_render_answers_exist_at_all():
    """Guards the guard: if the pages are renamed or deleted, the sweeps below
    would pass over an empty set and prove nothing."""
    rendering = _modules_rendering_answers()
    assert rendering, "no screen renders AnswerList — this file is asserting nothing"


def test_answer_screens_take_their_nodes_only_from_ask():
    """The property A9 actually needs.

    A page that fetched from `/graph` or `/query` and assembled something
    answer-shaped would be a **third** implementation of a question that already
    has one handler — free to disagree with REST and MCP, and certain to, since
    nothing compares them.

    Deliberately a denylist of *other node sources* rather than an allowlist of
    imports: a page legitimately imports plenty (settings, toasts, icons), and an
    allowlist would fail on the next harmless one and get relaxed.
    """
    forbidden = (
        "queryText", "queryTextStream", "getGraphLabels", "getKnowledgeGraph",
        "weaveTasks", "weaveChain", "studioArtifacts", "getDocuments",
    )
    offenders = []
    for path in _modules_rendering_answers():
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if re.search(rf"\b{name}\b", source):
                offenders.append(f"{path.relative_to(_UI)} imports {name}")

    assert not offenders, (
        "a screen rendering answers has a second source of nodes:\n  "
        + "\n  ".join(offenders)
    )


def test_no_screen_calls_axios_directly():
    """The escape hatch that would make every check above decorative.

    `axiosInstance` is exported for the client module's own use. A component
    reaching for it can request anything, including a hand-rolled `/ask` call
    that skips `ASK_ANCHOR` entirely.
    """
    offenders = []
    for path in sorted(_UI.rglob("*.tsx")):
        if re.search(r"\baxiosInstance\b", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_UI)))

    assert not offenders, (
        "a component uses axiosInstance directly rather than the API client:\n  "
        + "\n  ".join(offenders)
    )
