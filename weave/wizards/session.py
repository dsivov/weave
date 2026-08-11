"""The interview, and the diffs it produces (R37, R39).

**No server-side session state, and that is a design decision rather than an
omission.** The obvious wizard keeps an interview in a dict keyed by session id.
That works until a second worker exists, at which point half the requests land on
a process that has never heard of the session — no error, no log, just a wizard
that forgets. It is the same class of failure as the in-process bus under
gunicorn (A7, D-019), and W4's lens says the same thing: do not add state a
second worker would have to share.

So the flow is stateless. :func:`plan_for` returns the questions and what the
template would install; the client holds the answers and sends them to
:func:`propose_diffs`, which is a **pure function** of (template, answers). The
same property that makes it multi-worker-safe makes it testable without HTTP.

**What it produces is diffs, never files** (A8). `propose_diffs` returns
`ArtifactDiff` objects for the `rbac` and `lifecycle` ledger kinds, which the
router signs through the same `DiffEngine` the Studio uses. There is no
wizard-only write path.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional

from weave_core.studio.schema import ArtifactDiff

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

#: Template ids, in the order a chooser should show them — simplest first.
TEMPLATES = ("solo", "reviewed")

#: The ledger kinds a wizard run can change. Deliberately narrow: these are the
#: two A8 names, and a wizard that could rewrite arbitrary artifact kinds would
#: be a general editor with an interview bolted on.
WIZARD_KINDS = ("rbac", "lifecycle")


class WizardError(ValueError):
    """A template that does not exist, or answers that do not fit one."""


def _strip_comment(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k != "_comment"}
    return obj


def load_template(template_id: str) -> Dict[str, Any]:
    """Read one template. Raises rather than defaulting to a "safe" fallback —
    silently installing governance nobody asked for is worse than an error."""
    if template_id not in TEMPLATES:
        raise WizardError(
            f"no template '{template_id}'; available: {', '.join(TEMPLATES)}"
        )
    path = os.path.join(TEMPLATE_DIR, f"{template_id}.json")
    with open(path, encoding="utf-8") as fh:
        return _strip_comment(json.load(fh))


def plan_for(template_id: str) -> Dict[str, Any]:
    """The interview plan: what will be asked, and what the answers will change.

    Returns the questions *and* a summary of the artifacts a run would write, so
    the person can see the shape of the change before answering anything. A
    wizard that reveals its effect only at the end is one people click through.
    """
    template = load_template(template_id)
    return {
        "template": template["id"],
        "title": template["title"],
        "when_to_use": template.get("when_to_use", ""),
        "questions": template.get("questions", []),
        "installs": {
            "rbac": sorted(template.get("rbac", {}).get("roles", {})),
            "lifecycle": sorted(template.get("lifecycle", {}).get("machines", {})),
        },
        "kinds": list(WIZARD_KINDS),
    }


def catalogue() -> List[Dict[str, Any]]:
    """Every template, for a chooser."""
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "when_to_use": t.get("when_to_use", ""),
        }
        for t in (load_template(tid) for tid in TEMPLATES)
    ]


# ── answers → artifacts ──────────────────────────────────────────────────────


def _apply_answers(template: Dict[str, Any], answers: Dict[str, Any]) -> Dict[str, Any]:
    """Fold the interview's answers into the template's artifacts.

    Every branch here is a *documented* question from the template rather than a
    free-form transformation: an answer can narrow what is installed, never
    invent a grant the template did not offer. That keeps the diff reviewable —
    someone signing it can compare it against the template they chose.
    """
    rbac = copy.deepcopy(template.get("rbac", {}))
    lifecycle = copy.deepcopy(template.get("lifecycle", {}))

    present = answers.get("roles_present")
    if present:
        unknown = [r for r in present if r not in rbac.get("roles", {})]
        if unknown:
            raise WizardError(
                f"template '{template['id']}' has no role(s) {unknown}; "
                f"it defines {sorted(rbac.get('roles', {}))}"
            )
        rbac["roles"] = {r: g for r, g in rbac["roles"].items() if r in present}
        # A transition gated on a role nobody holds is a dead end — the task
        # would reach a state it can never leave. Drop those role gates rather
        # than leave a machine that traps work.
        for machine in lifecycle.get("machines", {}).values():
            for transition in machine.get("transitions", []):
                roles = transition.get("roles")
                if roles:
                    kept = [r for r in roles if r in present]
                    if kept:
                        transition["roles"] = kept
                    else:
                        transition.pop("roles")   # ungated rather than unreachable

    if answers.get("developers_self_approve"):
        # Explicitly asked for, and the template warns what it costs. Recorded in
        # the diff like anything else, so "who removed the review gate" has an
        # answer.
        for machine in lifecycle.get("machines", {}).values():
            for transition in machine.get("transitions", []):
                if transition.get("to") == "approved" and transition.get("roles"):
                    if "developer" not in transition["roles"]:
                        transition["roles"] = [*transition["roles"], "developer"]

    if answers.get("agents_may_merge"):
        grants = rbac.get("roles", {}).get("developer")
        if grants is not None and "invoke:MergeToMain" not in grants:
            grants.append("invoke:MergeToMain")

    return {"rbac": rbac, "lifecycle": lifecycle}


def propose_diffs(
    template_id: str,
    answers: Optional[Dict[str, Any]] = None,
    *,
    current: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> List[ArtifactDiff]:
    """The wizard's output: signed-ledger diffs, one per kind it changes.

    `current` maps kind → the artifact currently in the workspace (or None), so
    each diff records the version it was drafted against. That is what makes the
    P3.3 stale-write check work for a wizard run: two people setting up the same
    workspace at once is exactly the race, and the second one should be told.
    """
    template = load_template(template_id)
    artifacts = _apply_answers(template, answers or {})
    current = current or {}

    diffs: List[ArtifactDiff] = []
    for kind in WIZARD_KINDS:
        after = artifacts.get(kind)
        if not after:
            continue
        before = current.get(kind)
        from_version = before.get("version") if before else None
        diffs.append(
            ArtifactDiff(
                kind=kind,
                artifact_id=kind,
                to_version=int(from_version or 0) + 1,
                from_version=from_version,
                delta={"before": before or {}, "after": after},
                # Always true: RBAC and lifecycle *are* behaviour. This forces an
                # approver and a reason at sign-off, so a governance change
                # cannot be attributed to nobody.
                behaviour_changed=True,
                origin="authoring",
            )
        )
    return diffs
