"""What an operator sees on a documented install describes *this* product (W25–W28).

All four came from executing P8's install spine rather than reading it, and none
of them trips the name-guard — because the guard checks **spellings** (A3), and
these are inherited *content* that no longer describes the product. A half-dozen
sentences were carried over inside files that were carried over, and they went on
saying what they always said.

* **W25** — `weave up` warned about a missing `.env`, then asked *"Do you want to
  continue? (yes/no)"* and exited on anything but `yes`. It looked for a file
  Weave never writes (`weave init` writes `weave.env` in the *working*
  directory), and the prompt is guarded by `sys.stdin.isatty()` — so an operator
  following the guide by hand was stopped while every script sailed past.
* **W26** — the splash's second line advertised the parent product; every start
  logged `Ignoring workers=2`; the log went into whatever directory the operator
  was standing in.
* **W28** — the API description and several route descriptions still called this
  a RAG system.

**What this file covers and what it does not.** It asserts that the *first
screen* — splash, help, and the public API document — makes no claim belonging to
another product. It does not adjudicate content that is merely *wrong*: an
extraction prompt that teaches from a sci-fi story, a wizard template carrying
the parent's choices. Those need a reader, not a matcher.

The related guard, that the API description names no capability the server does
not serve, is `tests/test_the_api_describes_what_it_serves.py` (D-044).
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Phrases that assert **this product is the one it was forked from**.
#:
#: Phrases, not tokens, and the distinction is the whole design. A bare "RAG" is
#: the *technique* — `/query` really does perform retrieval-augmented generation,
#: and "Comprehensive RAG query endpoint" describes an endpoint the way "SSE
#: stream" would. *"the RAG system"* and *"Lightweight RAG Server"* describe a
#: **product**, and that product is not this one.
#:
#: Banning the token would have flagged six honest endpoint descriptions and
#: taught the next person to add an exemption, which is how a guard stops
#: guarding.
PARENT_SELF_DESCRIPTION = (
    "RAG system",
    "RAG Server",
    "RAG server",
    "Lightweight RAG",
    "Ollama Model Emulation",
    "Ollama Emulating",
    "simulated Ollama",
    "simulated model",
)

#: Deliberately **not** listed above: `ollama` on its own.
#:
#: Two things answer to that name and only one was withdrawn (D-044). *Emulation*
#: is Weave pretending to be an Ollama server — removed, and it never existed.
#: *Binding* is Weave using Ollama as a server-side model backend, which A13
#: explicitly blesses and which `--llm-binding ollama` defaults to. The server's
#: `--help` carries ~40 `--ollama-embedding-*` options for that binding; a
#: grep-driven sweep would have taken them and broken a supported deployment.
OLLAMA_IS_A_BACKEND_NOT_A_COSTUME = True


def _offences(text: str) -> list[str]:
    return [p for p in PARENT_SELF_DESCRIPTION if p in text]


# ── W26 · the splash ─────────────────────────────────────────────────────────


def test_the_splash_describes_this_product():
    """The first thing an install prints.

    Read from the function's source rather than by running it — the splash wants
    a fully parsed `args` and a terminal, and neither is worth standing up to
    check a sentence.
    """
    from weave.server.utils import display_splash_screen

    source = inspect.getsource(display_splash_screen)
    offences = _offences(source)
    assert not offences, (
        f"the splash screen describes another product: {offences}"
    )


def test_the_splash_no_longer_advertises_a_model_it_does_not_serve():
    """D-044 reached the splash too: it printed an `Ollama Emulating Model` line
    whose value came from two flags that configured nothing."""
    from weave.server.utils import display_splash_screen

    assert "ollama_server_infos" not in inspect.getsource(display_splash_screen)


# ── W25 · startup asks no questions ──────────────────────────────────────────


def test_startup_never_waits_for_someone_to_type():
    """**The class, not the one prompt.**

    A server that blocks on stdin hangs under a process manager the moment it
    inherits a terminal, and — worse — behaves differently depending on whether a
    human is watching. `check_env_file` was guarded by `isatty()`, so it stopped
    an operator following the guide by hand and let every script through. That
    asymmetry is why it survived to now: every capture we took was under `nohup`.
    """
    # **Parsed, not grepped.** The first version matched text and flagged the
    # docstring in `check_env_file` that *explains* this fix — a guard that
    # cannot tell code from prose is one that gets an exemption added to it, and
    # the exemption is where the next prompt hides.
    offenders = []
    for rel in ("weave/server/app.py", "weave/server/gunicorn.py",
                "weave/server/utils.py", "weave/server/config.py"):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if isinstance(called, ast.Name) and called.id == "input":
                offenders.append(f"{rel}:{node.lineno}: input(...)")
            elif isinstance(called, ast.Attribute) and called.attr == "isatty":
                offenders.append(f"{rel}:{node.lineno}: .isatty()")
    assert not offenders, (
        "the server startup path asks a question or branches on having a "
        "terminal:\n  " + "\n  ".join(offenders)
        + "\n\n  Refuse loudly for what cannot proceed; print advice for what "
        "can. Do not ask."
    )


def test_the_configuration_note_names_the_file_weave_actually_writes():
    """It warned about `.env` while `weave init` writes `weave.env`, so the
    documented sequence reported a problem that did not exist."""
    from weave.server.utils import check_env_file

    source = inspect.getsource(check_env_file)
    assert "weave.env" in source
    assert "weave init" in source, "the note does not say how to fix it"


def test_the_note_is_advice_and_cannot_stop_a_server(tmp_path, monkeypatch):
    """Run, not read: it returns True with no configuration present at all."""
    from weave.server.utils import check_env_file

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEAVE_WORKING_DIR", str(tmp_path / "nothing-here"))
    assert check_env_file() is True


# ── W26 · defaults that disagree with themselves ─────────────────────────────


def test_the_worker_default_is_one_and_says_so():
    """Three disagreements in one flag: the help said *"default: from env or 1"*,
    the code used `DEFAULT_WOKERS = 2`, and **A7 refuses** two workers on the
    in-process bus — which is the default bus, because the default storage path
    is file-based and therefore single-operator. Every uvicorn start logged
    `Forcing workers=1 … (Ignoring workers=2)` as a result.
    """
    from weave.server import DEFAULT_WORKERS

    assert DEFAULT_WORKERS == 1

    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        from weave.server.config import parse_args

        args = parse_args()
    finally:
        sys.argv = argv
    assert args.workers == 1, (
        "the default is more than one worker, which A7 refuses on the default "
        "event bus — so the default configuration cannot start"
    )


def test_the_log_goes_to_the_working_directory():
    """1.2 MB after one run, written into whatever directory `weave up` happened
    to be run from — including a home directory or a checkout, and a second one
    started if you ran it from somewhere else."""
    for rel in ("weave/server/app.py", "weave/server/gunicorn_config.py"):
        source = (_REPO / rel).read_text(encoding="utf-8")
        assert 'os.getenv("WEAVE_LOG_DIR", os.getcwd())' not in source, (
            f"{rel} still defaults the log directory to the current directory"
        )
        assert "resolve_working_dir()" in source, (
            f"{rel} does not put the log in the working directory"
        )


# ── W28 · the public API document ────────────────────────────────────────────


@pytest.fixture(scope="module")
def openapi() -> dict:
    """The document a reader actually sees, at full configuration.

    Built rather than grepped: descriptions are assembled from decorators,
    docstrings and sub-apps, so the only faithful source is the served document.
    """
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        from weave.server.config import parse_args

        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = tempfile.mkdtemp(prefix="first-screen-")
    args.workers = 1
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    args.enable_weave = True
    args.use_quadruple = True

    from weave.server.app import create_app

    with TestClient(create_app(args)) as client:
        return client.get("/openapi.json").json()


def test_the_api_document_describes_this_product(openapi):
    """Every string in it — title, description, summaries, route descriptions."""
    offences = sorted(set(_offences(json.dumps(openapi))))
    assert not offences, (
        f"the public API document describes another product: {offences}\n\n"
        "  This is the tab the guide screenshots, and a reader can act on it."
    )


def test_the_server_help_describes_this_product():
    """`weave --help` and `weave up --help` were already clean, which localised
    the problem exactly: the wrapper was rebranded and the surface underneath it
    was not. This is the surface underneath."""
    argv = sys.argv
    sys.argv = ["weave-server", "--help"]
    buffer = io.StringIO()
    try:
        from weave.server.config import parse_args

        with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit):
            parse_args()
    finally:
        sys.argv = argv

    text = buffer.getvalue()
    assert text, "the server printed no help at all"
    offences = _offences(text)
    assert not offences, f"the server's own --help describes another product: {offences}"
