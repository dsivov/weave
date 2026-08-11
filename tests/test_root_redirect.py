"""The server root lands somewhere that works (W10).

`GET /` redirects to the UI, and the trailing slash is load-bearing. Verified
against the real server, with a stub build in place so the branch could run at
all:

    GET /        307 → /webui/       GET /webui   404       GET /webui/  200

So redirecting root to the slashless form put a browser on a **404** at the very
first URL a human types.

**What is asserted here is that observation, not a mechanism.** A plain
`FastAPI` + `StaticFiles` mount *does* redirect `/webui` → `/webui/` (307), so
the bare path is only unreachable because slash-redirection is inactive on this
app — `/health/` 404s too. I did not pin down what disables it, and rather than
encode a guess, the reproduction models the condition (`redirect_slashes=False`)
and the regression guard reads the real source. Saying "I observed this and did
not explain it" is worth more than a tidy explanation that might be wrong.

It survived five milestones for two reasons, and both are the same lesson:

1. `webui_assets_exist` is false wherever the UI has not been built — which is
   every environment this project's tests run in — so **that branch had never
   executed**;
2. nothing asserted the reachable case either.

That is W6 again from a new angle: the suite does not construct the server, so
anything that only exists once it is running is unverified.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_APP = pathlib.Path(__file__).resolve().parent.parent / "weave" / "server" / "app.py"


@pytest.fixture
def built_ui(tmp_path):
    """A directory that looks like a built UI: an index and one asset."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>Weave</title></head><body></body></html>"
    )
    (tmp_path / "app.js").write_text("// bundle\n")
    return tmp_path


def _app_with_root(built_ui, redirect_to: str) -> FastAPI:
    """The real structure in miniature: a mount at `/webui`, root redirecting.

    `redirect_slashes=False` models the observed condition — on the real server a
    bare `/webui` 404s rather than redirecting. With FastAPI's default the mount
    would 307 to the slashed form and this file would prove nothing.
    """
    app = FastAPI(redirect_slashes=False)

    @app.get("/")
    async def root():
        return RedirectResponse(url=redirect_to)

    app.mount("/webui", StaticFiles(directory=str(built_ui), html=True), name="webui")
    return app


# ── the mechanism: why the slash matters ─────────────────────────────────────


def test_a_mount_serves_its_index_only_on_the_slashed_path(built_ui):
    """The fact the fix rests on, asserted rather than assumed.

    If a future Starlette makes `/webui` resolve on its own, this test says so —
    and the redirect could be simplified deliberately rather than by accident.
    """
    app = _app_with_root(built_ui, "/webui/")
    with TestClient(app) as client:
        assert client.get("/webui/").status_code == 200
        assert client.get("/webui").status_code == 404, (
            "the bare mount path resolved — if that is now true of the real "
            "server too, W10's fix is no longer load-bearing and app.py's "
            "reasoning should be revisited rather than left as folklore"
        )


def test_the_slashless_redirect_lands_a_browser_on_a_failure(built_ui):
    """W10 reproduced. This is what root did for five milestones — a 404 at the
    first URL anyone types."""
    app = _app_with_root(built_ui, "/webui")
    with TestClient(app) as client:
        landed = client.get("/", follow_redirects=True)

    assert landed.status_code == 404, (
        f"expected the slashless redirect to land on a 404, got "
        f"{landed.status_code} — the bug this guards against may have changed shape"
    )


def test_the_slashed_redirect_lands_on_the_ui(built_ui):
    """The fix: following root from a browser reaches the actual page."""
    app = _app_with_root(built_ui, "/webui/")
    with TestClient(app) as client:
        landed = client.get("/", follow_redirects=True)

    assert landed.status_code == 200
    assert "<title>Weave</title>" in landed.text


def test_assets_under_the_mount_still_serve(built_ui):
    """A redirect that reached an index whose assets 404 would be a worse bug
    than the one this replaces, because the page would render blank."""
    app = _app_with_root(built_ui, "/webui/")
    with TestClient(app) as client:
        assert client.get("/webui/app.js").status_code == 200


# ── the real app keeps the fix ───────────────────────────────────────────────


def test_the_server_redirects_root_to_the_slashed_path():
    """A regression guard on `app.py` itself.

    Source-level on purpose: the branch it protects only runs when the UI has
    been built, which is never in this environment — so an assertion about
    behaviour here would silently test nothing. Reading the source is the honest
    way to check a branch the test environment cannot enter.
    """
    source = _APP.read_text(encoding="utf-8")
    assert 'RedirectResponse(url="/webui/")' in source, (
        "root no longer redirects to the slashed mount path — a bare /webui 404s "
        "(W10)"
    )
    assert 'RedirectResponse(url="/webui")' not in source


def test_the_reason_is_recorded_next_to_the_code():
    """A one-character fix with no explanation is one someone tidies away."""
    source = _APP.read_text(encoding="utf-8")
    assert "trailing slash is load-bearing" in source
