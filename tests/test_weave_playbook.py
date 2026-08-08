"""P2 — the role kits (onboarding surface).

Every participant operates as a role, not a person. These check the role
directory, the per-role kit bundle (mcp_config + CLAUDE.md + governed
actions/endpoints), and that the kit stays consistent with the preset's RBAC
grants — a role's kit should not advertise an action RBAC won't let it invoke.
"""

from __future__ import annotations

import json
import os

import pytest

from weave.team import playbook


@pytest.mark.offline
def test_role_directory_lists_all_five_roles():
    roles = {r["role"]: r for r in playbook.roles()}
    assert set(roles) == {"manager", "architect", "developer", "integrator", "lead"}
    # runtimes and the human-only / optional flags are surfaced
    assert roles["manager"]["human_only"] is True
    assert roles["developer"]["runtime"] == "autonomous"
    assert roles["lead"]["optional"] is True and roles["lead"]["runtime"] == "lead"


@pytest.mark.offline
def test_role_kit_bundle_is_complete():
    kit = playbook.role_kit("developer", "proj", "https://cg.example.com/")
    assert kit["workspace"] == "proj" and kit["role"] == "developer"
    # the MCP surface is workspace-scoped and points at the server
    mcp = kit["mcp_config"]["mcpServers"]["weave"]
    assert mcp["url"] == "https://cg.example.com/mcp"
    assert mcp["headers"]["WEAVE-WORKSPACE"] == "proj"
    # the CLAUDE.md renders the loop + role, and the URLs have no double slash
    assert "Weave — Developer (developer)" in kit["claude_md"]
    assert "https://cg.example.com//" not in kit["claude_md"]
    assert kit["loop"] and kit["endpoints"] and kit["slash_commands"]
    assert kit["playbook_url"].endswith("/workspace/playbook?role=developer")


@pytest.mark.offline
def test_unknown_role_raises():
    with pytest.raises(KeyError):
        playbook.role_kit("wizard", "proj", "http://x")


@pytest.mark.offline
def test_claude_md_reuse_first_is_taught_to_builders():
    # the reuse-first principle must reach the roles that write code
    for role in ("architect", "developer"):
        md = playbook.claude_md(role, "w", "http://x")
        assert "reuse" in md.lower() and "reinvent" in md.lower()


@pytest.mark.offline
def test_onboarding_skill_bridge_is_wired_to_the_authoring_roles():
    # Manager authors vision/requirements; Architect owns make-workplan → tasks.
    assert playbook.ROLES["manager"]["skills"] == ["/new-project", "/write-blog", "/write-drp"]
    assert "/make-workplan" in playbook.ROLES["architect"]["skills"]
    assert "/write-rfc" in playbook.ROLES["architect"]["skills"]
    # execution roles author no methodology docs
    assert playbook.ROLES["developer"]["skills"] == []
    assert playbook.ROLES["integrator"]["skills"] == []

    # the CLAUDE.md tells authoring roles to ingest docs so the project is retrievable
    for role in ("manager", "architect"):
        md = playbook.claude_md(role, "w", "http://x")
        assert "Methodology skills" in md and "POST /documents/text" in md
        assert "/make-workplan" in md if role == "architect" else True
    # a developer kit has no methodology-skills section
    assert "Methodology skills" not in playbook.claude_md("developer", "w", "http://x")


@pytest.mark.offline
def test_kit_actions_are_within_rbac_grants():
    """A role's kit must not advertise an action its RBAC grant forbids. `lead`
    is a composite (developer + architect claims), so its allowance is the union
    of the roles it is composed of."""
    rbac_path = os.path.join(os.path.dirname(playbook.__file__), "preset", "rbac.json")
    with open(rbac_path, encoding="utf-8") as fh:
        grants = json.load(fh)["roles"]

    def allowance(role: str) -> set:
        spec = playbook.ROLES[role]
        composed = spec.get("composed_of")
        if composed:
            out: set = set()
            for c in composed:
                out |= allowance(c)
            return out
        return set(grants.get(role, []))

    for role, spec in playbook.ROLES.items():
        allowed = allowance(role)
        if "*" in allowed:
            continue                         # manager is unrestricted
        for action in spec["actions"]:
            assert f"invoke:{action}" in allowed, f"{role} kit lists {action} without an RBAC grant"


@pytest.mark.offline
def test_lead_is_a_composite_persona_not_a_standalone_role():
    """`lead` never authenticates as a `lead` token — it is a human holding the
    developer + architect claims. So it must NOT be a standalone RBAC/planner role
    (the auth layer is single-role); the enforcement layers only know its parts."""
    from weave.team.coordinator import PLANNER_ROLES

    rbac_path = os.path.join(os.path.dirname(playbook.__file__), "preset", "rbac.json")
    with open(rbac_path, encoding="utf-8") as fh:
        grants = json.load(fh)["roles"]

    lead = playbook.ROLES["lead"]
    assert lead["composed_of"] == ["developer", "architect"]
    assert "lead" not in grants          # not a real RBAC role
    assert "lead" not in PLANNER_ROLES   # plans as architect, not as "lead"
