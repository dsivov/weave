"""The diagram editor accepts what mermaid accepts, and explains its failures (U18, U19).

Found by using the product: reviewing a real diagram and trying to update it.
**The save path was faultless** — signed by the authenticated principal, reason
required, rules gate PASS, and every refusal said the source was still intact and
meant it. Opening was where the gaps were.

* **U18** — the editor matched `/^flowchart\\s+(TD|LR|BT|RL)/`, so `flowchart TB`
  (mermaid's own preferred spelling of that direction) and every `graph` form
  were rejected by the editor while the viewer beside it rendered them. The
  result was an unopenable artifact: intact source on the server, empty canvas,
  and a message naming the one spelling the editor happened to accept.
* **U19** — an edge ending on a subgraph is ordinary mermaid, and dagre throws
  on it. Two defects on that line: the layout could not handle a legal
  construct, **and the raw `TypeError` was rendered to the reader as the
  explanation**. A manager cannot act on *"setting 'rank'"*.

**What is tested where.** The behaviour — every header form, the subgraph
fixtures, the layout — is in
`weave-ui/src/features/next/diagram-editor/__tests__/parser.test.ts`, because it
needs the parser to actually run. This file holds the two claims that are about
the *class* rather than about a case: that the grammar is the one mermaid has,
and that no failure path anywhere in this feature can put a JavaScript error in
front of a reader.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent
_EDITOR = _REPO / "weave-ui" / "src" / "features" / "next" / "diagram-editor"
_PARSER = _EDITOR / "lib" / "parser.ts"
_DIAGRAMS = _REPO / "weave-ui" / "src" / "features" / "next" / "pages" / "Diagrams.tsx"

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _code(path: pathlib.Path) -> str:
    return _COMMENTS.sub("", path.read_text(encoding="utf-8"))


def _editor_header_pattern() -> re.Pattern:
    """The editor's own regex, lifted out of the source and compiled here.

    Deliberately not a copy: a second spelling of the grammar would drift from
    the first, and the drift would look exactly like the defect this closes.
    """
    m = re.search(r"const HEADER_RE =\s*\n?\s*/(.+?)/\n", _PARSER.read_text(encoding="utf-8"))
    assert m, "HEADER_RE is no longer a literal regex in parser.ts"
    return re.compile(m.group(1))


#: **Measured, not remembered.** Every one of these was run through
#: `mermaid.parse` against the mermaid version this project pins (11.16.1) and
#: accepted; `flowchart XX` is the only neighbouring form mermaid itself
#: refuses. TB/TD and v are the same direction, as are BT/^, LR/> and RL/<.
MERMAID_ACCEPTS = (
    "flowchart TD", "flowchart TB", "flowchart BT", "flowchart RL", "flowchart LR",
    "flowchart v", "flowchart ^", "flowchart >", "flowchart <", "flowchart",
    "graph TD", "graph TB", "graph BT", "graph RL", "graph LR",
    "graph v", "graph ^", "graph >", "graph <", "graph",
    "flowchart TD;", "graph TB;",
)


def test_the_editor_accepts_every_header_mermaid_accepts():
    """U18, as a class rather than as the two spellings that were reported."""
    pattern = _editor_header_pattern()
    rejected = [h for h in MERMAID_ACCEPTS if not pattern.match(h)]
    assert not rejected, (
        "the editor rejects headers the viewer renders: " + ", ".join(rejected)
    )


def test_the_editor_does_not_accept_what_mermaid_refuses():
    """The other side of the same rule — a grammar that accepts everything is not
    a grammar, and would open a diagram the viewer cannot draw."""
    pattern = _editor_header_pattern()
    assert not pattern.match("flowchart XX"), "the editor accepts a direction mermaid rejects"
    assert not pattern.match("flowchartish TD"), "a longer word starting with the keyword is not a header"
    assert not pattern.match("A --> B"), "an ordinary edge line is not a header"


def test_every_mermaid_header_in_this_repositorys_own_documents_opens():
    """The gate, in the manager's words.

    The corpus is small today — this repository writes `flowchart LR` — and that
    is the point: the assertion is over whatever is actually there, so a
    document added later in a form the editor cannot open fails here rather than
    on somebody's screen.

    **Only inside a mermaid block.** The first version matched any line starting
    with `graph`, and this project's own prose begins sentences with *"graph
    core and CGR3 retrieval…"* — a test stricter than the truth, which is the
    version of a guard that gets switched off rather than fixed.
    """
    pattern = _editor_header_pattern()
    blocks: list[tuple[str, str]] = []
    for path in sorted(_REPO.glob("docs/**/*.md")):
        for block in re.findall(r"^```mermaid\s*\n(.*?)^```", path.read_text(encoding="utf-8"),
                                re.S | re.M):
            blocks.append((str(path.relative_to(_REPO)), block))
    for path in sorted(_REPO.glob("docs/**/*.html")):
        for block in re.findall(r'<pre[^>]*class="[^"]*mermaid[^"]*"[^>]*>(.*?)</pre>',
                                path.read_text(encoding="utf-8"), re.S | re.I):
            blocks.append((str(path.relative_to(_REPO)), block))

    found, unopenable = set(), []
    for source, block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("%%"):
                continue
            # **Flowcharts only.** `pie`, `classDiagram` and `sequenceDiagram`
            # are other mermaid diagram types that this repository also uses and
            # that a flowchart editor is not expected to open — scoped by the
            # keyword, so a flowchart written in a spelling the editor rejects
            # is still an offender rather than being filtered out with them.
            if re.match(r"^(?:flowchart|graph)(?![\w-])", line):
                found.add(line)
                if not pattern.match(line):
                    unopenable.append(f"{source}: {line}")
            break   # the header is the first statement of the block
    assert found, "no mermaid flowcharts found in docs/ — has the corpus moved?"
    assert not unopenable, (
        "this repository's own documents contain flowcharts the editor cannot "
        "open:\n  " + "\n  ".join(unopenable)
    )


# ── a stack frame is not an explanation ──────────────────────────────────────


def test_no_failure_path_puts_a_javascript_error_in_front_of_a_reader():
    """**The class, not the instance.**

    The reported case was `parser.ts` returning `err.message`, which reached the
    screen as *"Cannot set properties of undefined (setting 'rank')"* inside an
    otherwise good sentence. Fixing that one `return` would leave every other
    catch in the feature free to do the same thing tomorrow.

    So the rule is: in this feature, a caught error's `.message` is read in
    exactly one place — `lib/errors.ts`, which decides whether it was written
    for a reader — or logged to the console, where the person who can use a
    stack frame will look.

    **Widening this from the one reported `return` found two more.**
    `PreviewPanel` and `MermaidLiveSection` both rendered `err.message` straight
    from `mermaid.render`. Those are usually good — mermaid's diagnostics are
    about the reader's own source — but the same line would hand over a
    `TypeError` from mermaid's internals without noticing, which is U19 exactly.
    They now go through the same helper.
    """
    #: Lines that mention `.message` without rendering it. Annotated rather than
    #: pattern-matched: "is this string going to a screen" is not something a
    #: regex can answer, and a silent exemption is how a guard stops guarding.
    ALLOWED = {
        # Compares against a sentinel to tell "the user cancelled the file
        # picker" from a real failure; the message shown is 'Invalid file'.
        "components/SettingsPopover.tsx",
        # The one place allowed to read a message, and the reason this rule can
        # be a rule.
        "lib/errors.ts",
    }
    offenders = []
    files = sorted(_EDITOR.rglob("*.ts")) + sorted(_EDITOR.rglob("*.tsx")) + [_DIAGRAMS]
    for path in files:
        if "__tests__" in path.parts:
            continue
        try:
            rel = str(path.relative_to(_EDITOR))
        except ValueError:
            rel = path.name
        if rel in ALLOWED:
            continue
        for i, line in enumerate(_code(path).splitlines(), 1):
            if ".message" not in line and "String(err" not in line and "String(e)" not in line:
                continue
            if line.lstrip().startswith("console."):
                continue
            offenders.append(f"{path.relative_to(_REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "a caught JavaScript error is rendered rather than logged:\n  "
        + "\n  ".join(offenders)
        + "\n\n  Log it with console.error and show a sentence the reader can act "
        "on. If this line is genuinely not user-facing, move the .message into a "
        "console call."
    )


def test_the_layout_failure_still_opens_the_diagram():
    """A dagre refusal degrades to a bad arrangement, not to an empty canvas.

    Defence in depth behind the U19 fix: if dagre refuses for a reason we have
    not met, the reader gets a diagram they can see and drag, which is worth
    more than a correct-looking refusal.

    **The catch block itself, not the file.** The first version asserted that
    `gridFallback` appeared in `layout.ts`, which stayed true when the catch was
    changed to rethrow — the function was still *defined*, just never reached. A
    guard that checks a symbol exists is not checking that anything calls it.
    """
    layout = _code(_EDITOR / "lib" / "layout.ts")
    assert "dagre.layout(g)" in layout
    m = re.search(r"catch \(err\) \{(.*?)\n  \}", layout, re.S)
    assert m, "the dagre call is no longer wrapped in a catch"
    body = m.group(1)
    assert "gridFallback" in body, (
        "the layout's catch no longer falls back to a grid — a dagre refusal "
        "takes the whole diagram down again"
    )
    assert "throw" not in body, "the catch rethrows, so catching it achieves nothing"


def test_a_line_break_in_a_label_is_a_line_break():
    """`<br/>` is how mermaid writes one, and the preview pane honours it — so
    the canvas rendering it literally made the two panes disagree about the same
    source."""
    node = _code(_EDITOR / "components" / "NodeTypes" / "FlowNode.tsx")
    assert re.search(r"split\(/<br", node), (
        "a node label no longer splits on <br/>, so the canvas and the preview "
        "render the same source differently"
    )
    assert "dangerouslySetInnerHTML" not in node, (
        "a label is user content — handle the one tag we owe it, do not inject HTML"
    )
