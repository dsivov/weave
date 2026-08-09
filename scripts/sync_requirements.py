#!/usr/bin/env python3
"""Regenerate deploy/requirements.txt from environment.yml.

`environment.yml` is the single dependency manifest (D-006, A11). The container
image cannot use it — conda inside a container buys nothing, since the image is
already the environment — so this projects the same pins into a pip file.

It is a projection, never a second manifest. Adding a library here instead of in
environment.yml is the two-sources-of-truth defect R10 exists to catch, and
tests/test_dependency_parity.py fails the build if the two disagree.

    python scripts/sync_requirements.py
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

HEADER = """# Generated from environment.yml — DO NOT EDIT BY HAND.
#
# environment.yml is the single dependency manifest (D-006, A11). This file
# exists only because conda buys nothing inside a container: the image *is* the
# environment. It carries the same pins, and tests/test_dependency_parity.py
# fails if the two ever disagree.
#
# Regenerate with:  python scripts/sync_requirements.py
"""


def parse_environment(text: str) -> tuple[list[str], list[str]]:
    """Return (conda libraries, pip libraries), ignoring python/pip themselves."""
    conda: list[str] = []
    pip: list[str] = []
    in_pip = False
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        # Track the top-level key. Without this, `channels:` contributes
        # "conda-forge" as though it were a library — which it is not, and pip
        # would try to install it.
        top = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
        if top:
            section = top.group(1)
            in_pip = False
            continue
        if section != "dependencies":
            continue
        if re.match(r"^\s*-\s*pip:\s*$", line):
            in_pip = True
            continue
        m = re.match(r"^(\s*)-\s+(\S.*)$", line)
        if not m:
            continue
        indent, spec = len(m.group(1)), m.group(2).strip()
        if in_pip and indent >= 6:
            pip.append(spec)
            continue
        in_pip = False
        if spec.startswith(("python=", "pip")):
            continue
        conda.append(spec)
    return conda, pip


def render(conda: list[str], pip: list[str]) -> str:
    out = [HEADER, "", "# native / binary-heavy (conda-forge in environment.yml)"]
    for dep in conda:
        # conda writes `numpy>=1.24,<3`; pip understands that unchanged, but a
        # bare `name=1.2` is conda's single-equals pin and must become `==`.
        out.append(re.sub(r"^([A-Za-z0-9_.\-]+)=([0-9])", r"\1==\2", dep))
    out += ["", "# the rest"]
    out += pip
    return "\n".join(out) + "\n"


def main() -> int:
    conda, pip = parse_environment((REPO / "environment.yml").read_text())
    target = REPO / "deploy" / "requirements.txt"
    rendered = render(conda, pip)
    if "--check" in sys.argv:
        if target.read_text() != rendered:
            print("deploy/requirements.txt is stale — run scripts/sync_requirements.py",
                  file=sys.stderr)
            return 1
        print("deploy/requirements.txt matches environment.yml")
        return 0
    target.write_text(rendered)
    print(f"wrote {target.relative_to(REPO)} — {len(conda) + len(pip)} libraries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
