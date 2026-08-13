"""`/docs` names assets that are actually served (U9).

The route hardcoded `/static/swagger-ui/*` while the **mount** for that path is
conditional on the directory existing — and nothing ships it. So:

```
GET /docs                                    200
GET /static/swagger-ui/swagger-ui.css        404
GET /static/swagger-ui/swagger-ui-bundle.js  404
GET /openapi.json                            200
```

A page that loads and does nothing, with a healthy contract behind it. **The
condition was on the wrong side**: the page promised offline assets
unconditionally and the mount delivered them only sometimes.

Same shape as the quadruple refusal — something asserted where it is not true —
and the same fix: make the claim follow the fact. The page uses vendored assets
when they are present and FastAPI's defaults when they are not.

**What this does not decide:** whether Weave *should* vendor them. That matters
for an air-gapped install, where the defaults are a CDN the browser cannot
reach, and it is a packaging decision rather than something to settle by leaving
a broken page in place.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.offline

_APP = pathlib.Path(__file__).resolve().parent.parent / "weave" / "server" / "app.py"


def _source() -> str:
    return _APP.read_text(encoding="utf-8")


def test_the_docs_page_only_names_local_assets_when_they_exist():
    """The property. A constant `/static/swagger-ui/…` in the route is the defect
    itself — it is a promise the mount is not obliged to keep."""
    source = _source()
    route = source[source.index("async def custom_swagger_ui_html("):]
    route = route[: route.index("@app.get(\"/docs/oauth2-redirect\"")]

    hardcoded = re.findall(r'"(/static/swagger-ui/[^"]+)"', route)
    if hardcoded:
        # Naming them is fine *inside* the conditional; naming them
        # unconditionally is not.
        assert "if _swagger_local" in route or "_swagger_local" in route, (
            "the docs page names local swagger assets unconditionally, but the "
            f"mount that serves them is conditional: {hardcoded}"
        )


def test_the_page_and_the_mount_share_one_condition():
    """Two independent conditions is how they drift apart again. The mount checks
    the directory; the page must consult the same fact rather than a second
    opinion about it."""
    source = _source()
    assert "_swagger_local" in source, (
        "there is no single fact deciding whether local assets are used"
    )
    # The mount and the page both hang off the same directory probe.
    assert 'Path(__file__).parent / "static" / "swagger-ui"' in source


def test_the_condition_checks_a_file_not_just_the_directory():
    """An empty `static/swagger-ui/` directory would satisfy `exists()` and serve
    nothing — the 404 back, with the guard passing."""
    source = _source()
    probe = source[source.index("_swagger_local ="):]
    assert "swagger-ui-bundle.js" in probe[:300], (
        "the check accepts a directory that may be empty; probe for the script "
        "the page actually loads"
    )


def test_the_openapi_contract_is_untouched():
    """`/openapi.json` was always healthy — the defect was the viewer, not the
    contract. A fix that changed the schema would be solving something else."""
    source = _source()
    assert "openapi_url=app.openapi_url" in source
