"""Every documented step maps to a command that exists (M6 gate).

The gate is *a clean machine reaching a live fleet by the published steps only*,
and the failure it guards against is quiet: a guide that still reads correctly
while naming a command that was renamed, folded into another, or never built.
Nobody notices until someone follows it — and by then they are the person least
able to work out what changed.

So the guides are parsed and every `weave …` invocation in them is checked
against the real parser. A step with no command fails here rather than on a
stranger's terminal.

The reverse is deliberately **not** asserted: a command with no guide entry is
fine. `weave user promote` does not need a paragraph in the first-fleet walkthrough,
and demanding one would push filler into the docs to satisfy a test.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from weave.cli import build_parser

pytestmark = pytest.mark.offline

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_GUIDES = _ROOT / "docs" / "guides"

#: `weave <group> <action> …` inside a fenced block or inline code.
_WEAVE_CMD = re.compile(r"\bweave\s+([a-z][\w-]*)(?:\s+([a-z][\w-]*))?")

#: Placeholders a reader is expected to substitute; not literal arguments.
_PLACEHOLDER = re.compile(r"^<.*>$")


def _guides() -> list[pathlib.Path]:
    return sorted(_GUIDES.glob("*.md"))


def _documented_commands() -> list[tuple[pathlib.Path, str, str | None]]:
    found = []
    for path in _guides():
        for line in path.read_text(encoding="utf-8").splitlines():
            # Skip prose that merely mentions the product name.
            for match in _WEAVE_CMD.finditer(line):
                group, action = match.group(1), match.group(2)
                if _PLACEHOLDER.match(group):
                    continue
                found.append((path, group, action))
    return found


# ── the guides exist and are reachable ───────────────────────────────────────


def test_there_is_at_least_one_guide():
    assert _guides(), "docs/guides/ is empty — the M6 gate is followed steps"


def test_every_guide_names_at_least_one_command():
    """A guide with no commands is a description, and the gate is about steps
    someone can follow."""
    for path in _guides():
        assert _WEAVE_CMD.search(path.read_text(encoding="utf-8")), (
            f"{path.name} documents no runnable step"
        )


# ── every documented step is real ────────────────────────────────────────────


def test_every_documented_weave_command_exists():
    """The gate criterion. A renamed subcommand fails here, not on a stranger's
    machine on their first day."""
    parser = build_parser()
    groups = {
        name
        for action in parser._subparsers._group_actions           # noqa: SLF001
        for name in action.choices
    } if parser._subparsers else set()                            # noqa: SLF001

    unknown = []
    for path, group, _action in _documented_commands():
        if group not in groups:
            unknown.append(f"{path.name}: `weave {group}` is not a command")

    assert not unknown, (
        "the guides name commands that do not exist:\n  " + "\n  ".join(unknown)
    )


def test_every_documented_subcommand_exists():
    """One level deeper: `weave migrate reviews` must have a `reviews`."""
    parser = build_parser()
    group_parsers = {}
    if parser._subparsers:                                        # noqa: SLF001
        for action in parser._subparsers._group_actions:          # noqa: SLF001
            group_parsers.update(action.choices)

    unknown = []
    for path, group, action in _documented_commands():
        if action is None or group not in group_parsers:
            continue
        sub = group_parsers[group]._subparsers                    # noqa: SLF001
        if sub is None:
            continue
        actions = {
            name
            for group_action in sub._group_actions                # noqa: SLF001
            for name in group_action.choices
        }
        # A flag or a positional argument, not a subcommand.
        if action.startswith("-") or not actions:
            continue
        if action not in actions:
            unknown.append(
                f"{path.name}: `weave {group} {action}` — "
                f"'{group}' has {sorted(actions)}"
            )

    assert not unknown, (
        "the guides name subcommands that do not exist:\n  " + "\n  ".join(unknown)
    )


def test_documented_commands_parse_with_their_flags():
    """`--dry-run` and friends are part of the step. A flag that was renamed
    makes the copied line fail exactly as a missing command would."""
    parser = build_parser()
    failures = []

    for path in _guides():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("weave "):
                continue
            argv = [
                token for token in stripped.split()[1:]
                if not _PLACEHOLDER.match(token)
            ]
            if not argv:
                continue
            try:
                parser.parse_args(argv)
            except SystemExit:
                failures.append(f"{path.name}: `{stripped}` does not parse")

    assert not failures, (
        "documented commands do not parse as written:\n  " + "\n  ".join(failures)
    )


# ── the guides say the things the gate rests on ──────────────────────────────


def test_the_guide_covers_the_steps_the_gate_measures():
    """The M6 gate is *clean machine → live fleet by the published steps only*.
    If a step is missing from the guide, the measurement is of something else."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in _guides()).lower()

    for needed, why in [
        ("weave doctor", "the seat check — nothing works without one"),
        ("weave user add", "the first administrator"),
        ("weave_token_secret", "the server refuses to start without it"),
        ("weave.server.app", "starting the server"),
        ("weave.devhost", "attaching a machine that carries developers"),
        ("dispatch", "putting developers to work"),
    ]:
        assert needed in text, f"the guides never mention {needed!r} — {why}"


def test_the_guide_does_not_promise_that_dispatch_starts_anything():
    """A15 is easiest to misdescribe in prose. Dispatch records intent; hosts
    reconcile on their next heartbeat, and a guide implying otherwise teaches the
    wrong mental model to every reader."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in _guides()).lower()
    assert "heartbeat" in text
    assert "nothing starts immediately" in text or "pulls, not a command" in text
