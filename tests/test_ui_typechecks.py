"""The UI type-checks (W8).

Three type errors sat in the UI for the whole build — `api/weave.ts`,
`ChatMessage.tsx`, `FileUploader.tsx` — because **the UI has never been built or
checked in any environment the tests run in**. `bun` is absent from the dev
container, and `.github/workflows/ci.yml` runs the UI on a separate job, so
nothing in the Python suite ever looked.

That is W6's shape again, one layer out: the suite does not construct the UI, so
anything only a compiler would notice went unnoticed. This closes it for the type
level, which is the part reachable without a bundler.

**Skipped rather than failed when the toolchain is absent**, with the reason
named — a test that silently passes when it could not run is the thing this
project keeps finding.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.offline

_UI = pathlib.Path(__file__).resolve().parent.parent / "weave-ui"
_TSC = _UI / "node_modules" / ".bin" / "tsc"


requires_ui_toolchain = pytest.mark.skipif(
    not _TSC.exists(),
    reason=(
        "the UI toolchain is not installed (weave-ui/node_modules/.bin/tsc is "
        "absent) — run `bun install` in weave-ui/ to type-check the UI. The UI "
        "is therefore UNCHECKED in this run, not proven clean (W8)."
    ),
)


@requires_ui_toolchain
def test_the_ui_has_no_type_errors():
    """`tsc --noEmit` over the whole UI.

    Not scoped to files a change touched: the errors this closes were all
    pre-existing, and a scoped check would have kept passing while they sat
    there.
    """
    completed = subprocess.run(
        [str(_TSC), "--noEmit", "-p", "tsconfig.json"],
        cwd=str(_UI), capture_output=True, text=True, timeout=900,
    )

    assert completed.returncode == 0, (
        "the UI does not type-check:\n"
        + (completed.stdout or completed.stderr or "").strip()[:4000]
    )


def test_the_build_command_is_recorded_even_though_it_needs_bun():
    """The build itself still needs `bun` — `vite.config.ts` uses `@/` aliasing
    that node cannot resolve when loading the config. Worth stating rather than
    leaving someone to discover it: a clean `tsc` is not a proven bundle, and the
    M6 gate's UI half is met by building on a machine that has bun.
    """
    package_json = (_UI / "package.json").read_text(encoding="utf-8")
    assert "bunx --bun vite build" in package_json
    assert shutil.which("bun") is None or True   # informational; never fails here
