"""Every asset `/docs` names is really served (U9, D-042).

**The companion test to `test_docs_page_names_assets_that_exist.py`, and it exists
because that one reads source.** Those tests assert the page and the mount share
a condition — a structural property, and the right one. They cannot notice that
`swagger-ui.css` was never copied, or arrived truncated, or that the mount is
declared for a path the app does not actually serve.

That gap is the whole of U9: `/docs` returned **200** with a 404 stylesheet and a
404 script. Reading the source would not have caught it; fetching the URLs would.

Now that the assets are **vendored** (D-042, so `/docs` works on an air-gapped
install), the live failure mode is a half-copied or stale refresh — which looks
exactly like a working repository until someone opens the page. So this walks the
rendered HTML, pulls out every URL it names, and fetches each one.

**Deliberately not asserting *which* assets are named.** The page names local
files when they are vendored and FastAPI's CDN defaults when they are not, and
both are correct — the property is that whatever it names, it serves. Only
same-origin URLs are fetched: a CDN URL is somebody else's uptime and this suite
does not depend on the network.
"""

from __future__ import annotations

import re
import sys

import pytest
from fastapi.testclient import TestClient

from weave.server.app import create_app
from weave.server.config import parse_args

pytestmark = pytest.mark.offline

#: `src="…"` / `href="…"` out of the Swagger page.
_ASSET = re.compile(r'(?:src|href)="([^"]+)"')

#: A truncated download still returns 200. These are the real floors — the
#: bundle is ~1.5 MB and the stylesheet ~180 KB, so anything this small is a
#: stub, an error page, or half a file.
_MIN_BYTES = {".js": 200_000, ".css": 20_000, ".png": 100}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """A real application, which is the point.

    **This is the first test in the suite that builds the server** — W6 has been
    open since M2 precisely because nothing did: routers were written and never
    mounted, and `app.state` was set before `app` existed, both caught only by
    someone starting it by hand. Everything `/docs` needs is decided inside
    `create_app`, so a test that stops short of constructing it can only read
    source, which is how U9 survived.

    `create_app` takes parsed args rather than reading the environment itself, so
    the arguments are built the way the entry points build them and pointed at a
    scratch directory — this must not touch anyone's working store.

    `parse_args()` reads `sys.argv`, which under pytest holds pytest's own flags,
    so argv is replaced for the call rather than passed in.
    """
    argv = sys.argv
    sys.argv = ["weave-server"]
    try:
        args = parse_args()
    finally:
        sys.argv = argv
    args.working_dir = str(tmp_path_factory.mktemp("docs-assets"))
    # `TestClient` is one process, so this is the truth rather than a convenience:
    # declaring more would trip A7's refusal, correctly — the in-process bus
    # cannot fan out across workers. Constructing the app runs the real startup
    # checks, which is most of why building it in a test is worth doing at all.
    args.workers = 1
    # And a real signing secret, for the same reason: `parse_args()` reads the
    # environment, so in a shell with no `WEAVE_TOKEN_SECRET` this inherits the
    # published default and `create_app` refuses to start — correctly (S1).
    # Without this the file errors on any machine that has not exported one,
    # which is every clean checkout. Set here rather than in the environment so
    # the test carries its own preconditions.
    args.token_secret = "a-signing-secret-for-tests-only-not-the-published-default"
    with TestClient(create_app(args)) as c:
        yield c


def test_the_docs_page_renders(client: TestClient) -> None:
    r = client.get("/docs")
    assert r.status_code == 200
    assert "SwaggerUIBundle" in r.text, "the page loaded but does not start Swagger UI"


def test_every_asset_the_page_names_is_served(client: TestClient) -> None:
    """The assertion U9 needed and nobody had."""
    page = client.get("/docs")
    assert page.status_code == 200

    named = _ASSET.findall(page.text)
    assert named, "the docs page names no assets at all — it cannot render"

    local = [u for u in named if u.startswith("/")]
    if not local:
        pytest.skip("no vendored assets in this checkout; the page uses CDN defaults")

    missing = []
    for url in sorted(set(local)):
        got = client.get(url)
        if got.status_code != 200:
            missing.append(f"{url} → HTTP {got.status_code}")
            continue
        floor = next((b for ext, b in _MIN_BYTES.items() if url.endswith(ext)), 1)
        if len(got.content) < floor:
            missing.append(
                f"{url} → only {len(got.content)} bytes, expected ≥ {floor} "
                "(truncated or a stub?)"
            )

    assert not missing, (
        "the docs page names assets the server does not serve:\n  "
        + "\n  ".join(missing)
        + "\n\n  This is U9 exactly: /docs answers 200 and the browser gets a blank "
          "page. If the vendored assets were refreshed, re-copy every file listed "
          "in weave/server/static/swagger-ui/PROVENANCE.md."
    )


def test_the_openapi_document_is_still_served(client: TestClient) -> None:
    """The contract behind the viewer. It was healthy throughout U9, and a
    docs-page change must not be able to break it."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json().get("paths"), "the OpenAPI document has no paths"
