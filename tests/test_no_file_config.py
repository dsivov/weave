"""No wizard path writes a server file, and none requires a restart (A8, M4 gate).

A8's sentence is the one this phase could most easily make false: *what the
runtime enforces is the signed ledger version, and roles, RBAC and lifecycle have
no server-file config path.* The obvious wizard — interview, write config,
restart — breaks it, and would still pass every behavioural test if the restart
happened to occur between assertions.

So this file asserts the negative directly, three ways, because a negative that
is only reasoned about tends to become false quietly:

1. a full wizard run touches **no file on disk** (watched, not assumed);
2. the change is enforced **in the same process**, with nothing reloaded;
3. no code path in `weave/wizards/` opens a file for writing at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from weave.wizards import propose_diffs
from weave.wizards.session import TEMPLATE_DIR
from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave_core.governance.rbac import InMemoryRbacStore, RbacService
from weave_core.studio.service import DiffEngine
from weave_core.studio.store import InMemoryStudioStore

pytestmark = pytest.mark.offline

WORKSPACE = "alpha"
_WIZARD_DIR = pathlib.Path(__file__).resolve().parent.parent / "weave" / "wizards"


def _engine():
    rbac = RbacService(InMemoryRbacStore())
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        rbac_service=rbac,
        lifecycle_service=lifecycle,
        now=lambda: 1.0,
    )
    return engine, rbac, lifecycle


# ── 1 · a run writes nothing to disk ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_full_wizard_run_writes_no_file(tmp_path, monkeypatch):
    """Watched rather than assumed: the working directory is empty before and
    after, and `open(..., 'w')` is trapped for the duration."""
    monkeypatch.chdir(tmp_path)

    opened_for_write = []
    real_open = open

    def _watched_open(file, mode="r", *args, **kwargs):
        if any(m in mode for m in ("w", "a", "x", "+")):
            opened_for_write.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _watched_open)

    engine, rbac, lifecycle = _engine()
    for diff in propose_diffs("reviewed", {}):
        await engine.apply(WORKSPACE, diff, approver="architect",
                           reason="wizard run", role="architect")

    assert opened_for_write == [], (
        "the wizard opened a file for writing — governance must live in the "
        f"signed ledger, not on disk (A8): {opened_for_write}"
    )
    assert list(tmp_path.iterdir()) == [], "the wizard left files behind"

    # …and it genuinely did something, or the assertion above is vacuous.
    assert rbac.get_summary(WORKSPACE)["exists"] is True
    assert lifecycle.get_summary(WORKSPACE)["exists"] is True


# ── 2 · enforced in-process, nothing reloaded ────────────────────────────────


@pytest.mark.asyncio
async def test_the_change_is_enforced_without_reloading_anything():
    """The same service object answers differently before and after. No restart,
    no re-read, no second source to keep in step."""
    engine, rbac, _lc = _engine()

    before = rbac.check(WORKSPACE, "integrator", "invoke", "MergeToMain").allowed
    for diff in propose_diffs("solo", {}):
        await engine.apply(WORKSPACE, diff, approver="architect",
                           reason="wizard run", role="architect")
    after = rbac.check(WORKSPACE, "integrator", "invoke", "MergeToMain").allowed

    assert before is True and after is False, (
        "the same service instance did not change its answer, so something "
        "outside it is holding the truth"
    )


# ── 3 · no write path exists in the package at all ───────────────────────────


@pytest.mark.offline
def test_no_wizard_module_opens_a_file_for_writing():
    """The structural version of the same rule.

    Test 1 proves this run wrote nothing; this proves no run could. Templates are
    read-only package data, so reads are expected and writes are not — a wizard
    that persisted anything itself would be the config path A8 forbids.
    """
    offenders = []
    for path in sorted(_WIZARD_DIR.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("open", "write_text", "write_bytes", "mkdir", "makedirs"):
                continue
            if name == "open":
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if not any(m in mode for m in ("w", "a", "x", "+")):
                    continue        # reading a template is fine
            offenders.append(f"{path.name}:{node.lineno} — {name}")

    assert not offenders, (
        "a wizard module writes to disk; governance goes through the signed "
        "ledger, never a file (A8):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.offline
def test_templates_are_read_only_package_data():
    """They ship with the package and are inputs, not state. If a run could edit
    one, the installed template would drift from the shipped one and "which
    template did we use" would stop having an answer."""
    templates = sorted(pathlib.Path(TEMPLATE_DIR).glob("*.json"))
    assert templates, "no templates shipped"

    import weave.wizards.session as session

    source = pathlib.Path(session.__file__).read_text(encoding="utf-8")
    assert "TEMPLATE_DIR" in source
    # The only use of the directory is to read from it.
    assert 'open(path, encoding="utf-8")' in source


@pytest.mark.offline
def test_the_wizard_declares_only_the_two_governance_kinds():
    """A wizard that could write arbitrary artifact kinds would be a general
    editor with an interview attached, and its blast radius would stop matching
    what a reviewer expects from "the setup wizard"."""
    from weave.wizards.session import WIZARD_KINDS

    assert set(WIZARD_KINDS) == {"rbac", "lifecycle"}
