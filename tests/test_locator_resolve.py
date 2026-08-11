"""Locators resolve against the revision they recorded, not against HEAD (R23).

The failure this prevents: someone reorganises the repository, and every artifact
that ever cited a file silently stops meaning anything. A review that said "see
`auth.py:40`" is a claim about the code *as it was when the review was written*,
so resolution has to be a claim about a commit, not about the current tree.

These tests build a real git repository and move a file, because that is the
scenario — `git show <rev>:<path>` versus opening a file — and a mocked resolver
would prove only that the mock was called.
"""

from __future__ import annotations

import subprocess

import pytest

from weave.model.locator import Locator, LocatorError
from weave.model.project_layout import (
    InMemoryProjectLayoutStore,
    NotRegistered,
    ProjectLayoutError,
    ProjectLayoutRegistry,
    browse_url,
)

ORIGINAL = "def authenticate(user):\n    return check(user)\n"
REWRITTEN = "def authenticate(user, workspace):\n    return check(user, workspace)\n"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A repository whose history contains a file that later moves and changes."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")

    (root / "auth.py").write_text(ORIGINAL)
    _git(root, "add", "auth.py")
    _git(root, "commit", "-q", "-m", "the revision an artifact cites")
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
    ).stdout.strip()

    # The reorganisation: the file moves into a package and its contents change.
    (root / "server").mkdir()
    (root / "auth.py").unlink()
    (root / "server" / "auth.py").write_text(REWRITTEN)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "move auth into server/ and rewrite it")

    return root, first


@pytest.fixture
def registry(repo):
    root, _ = repo
    reg = ProjectLayoutRegistry(InMemoryProjectLayoutStore())
    reg.register(
        "alpha", "weave",
        clone_url="https://github.com/example/weave.git",
        local_path=str(root),
        default_rev="main",
    )
    return reg


# ── R23 · the pinned revision is what resolves ───────────────────────────────


@pytest.mark.offline
def test_a_locator_resolves_at_its_recorded_revision(registry, repo):
    _, first = repo
    result = registry.resolve("alpha", Locator("weave", "auth.py", first))

    assert result["exists"] is True
    assert result["content"] == ORIGINAL, (
        "resolution returned the current contents, not the contents at the "
        "revision the artifact cited"
    )
    assert result["rev"] == first


@pytest.mark.offline
def test_a_file_that_moved_at_head_still_resolves(registry, repo):
    """The reorganisation case, stated as the gate states it.

    `auth.py` does not exist at HEAD — it is `server/auth.py` now. An artifact
    citing the old path at the old revision must still resolve, or history rots
    every time someone tidies up.
    """
    root, first = repo
    assert not (root / "auth.py").exists(), "fixture did not actually move the file"

    at_pinned_rev = registry.resolve("alpha", Locator("weave", "auth.py", first))
    assert at_pinned_rev["exists"] is True
    assert at_pinned_rev["content"] == ORIGINAL

    # And the same path against HEAD is honestly reported as gone, rather than
    # silently resolving to whatever happens to be there now.
    at_head = registry.resolve("alpha", Locator("weave", "auth.py", "HEAD"))
    assert at_head["exists"] is False
    assert at_head["reason"]


@pytest.mark.offline
def test_the_new_path_resolves_at_head(registry):
    result = registry.resolve("alpha", Locator("weave", "server/auth.py", "HEAD"))
    assert result["exists"] is True and result["content"] == REWRITTEN


@pytest.mark.offline
def test_a_dangling_locator_reports_why_without_pretending(registry, repo):
    """What the resolver check counts (R24). `exists` is False and there is a
    reason; there is no partial content and no exception."""
    _, first = repo
    result = registry.resolve("alpha", Locator("weave", "no/such/file.py", first))

    assert result["exists"] is False
    assert "content" not in result
    assert result["reason"]


# ── the locator itself ───────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("missing", ["repo", "path", "rev"])
def test_a_locator_without_a_revision_is_refused(missing):
    """A locator missing `rev` would resolve against a moving target, which is
    the whole failure mode. Refuse it at construction, not at read time."""
    fields = {"repo": "weave", "path": "auth.py", "rev": "abc123"}
    fields[missing] = ""
    with pytest.raises(LocatorError):
        Locator(**fields)


@pytest.mark.offline
def test_a_locator_round_trips_through_node_properties():
    locator = Locator("weave", "docs/rfc.md", "abc123", anchor="L40")
    props = locator.to_node_properties()

    assert props == {
        "locator_repo": "weave",
        "locator_path": "docs/rfc.md",
        "locator_rev": "abc123",
        "locator_anchor": "L40",
    }
    assert Locator.from_node_properties(props) == locator


@pytest.mark.offline
def test_a_node_with_no_locator_reads_as_none_and_a_partial_one_raises():
    """Most nodes legitimately carry no locator — a Role is not an artifact. A
    *partial* locator is a bug, and one that would otherwise surface far from
    its cause."""
    assert Locator.from_node_properties({"entity_type": "Role"}) is None

    with pytest.raises(LocatorError):
        Locator.from_node_properties({"locator_repo": "weave", "locator_path": "a.py"})


@pytest.mark.offline
def test_an_anchor_is_omitted_rather_than_written_empty():
    props = Locator("weave", "a.py", "abc123").to_node_properties()
    assert "locator_anchor" not in props


# ── URLs a human can click ───────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize("clone_url", [
    "https://github.com/example/weave.git",
    "https://github.com/example/weave",
    "git@github.com:example/weave.git",
    "ssh://git@github.com/example/weave.git",
])
def test_the_browse_url_is_built_from_either_clone_url_form(clone_url):
    url = browse_url(clone_url, "auth.py", "abc123")
    assert url == "https://github.com/example/weave/blob/abc123/auth.py"


@pytest.mark.offline
def test_an_anchor_reaches_the_url():
    url = browse_url("https://github.com/example/weave", "auth.py", "abc123", "L40")
    assert url.endswith("/blob/abc123/auth.py#L40")


@pytest.mark.offline
@pytest.mark.parametrize("clone_url", [
    "file:///srv/mirrors/weave",     # a valid clone URL; nothing to browse
    "git://example.com/weave.git",   # likewise
    "/srv/mirrors/weave",            # a bare local path
    "",
    "not a url",
])
def test_an_unbrowsable_clone_url_yields_no_link_rather_than_a_guess(clone_url):
    """A wrong link is worse than no link: a reader follows it and concludes
    something either way."""
    assert browse_url(clone_url, "a.py", "abc123") == ""


@pytest.mark.offline
def test_the_web_scheme_is_not_silently_upgraded():
    """An `http` clone URL names a host that may not serve TLS. `ssh` names no
    web scheme at all, so https is the only reasonable assumption there."""
    assert browse_url("http://git.internal/org/weave", "a.py", "abc123").startswith(
        "http://git.internal/"
    )
    assert browse_url("ssh://git@git.internal/org/weave", "a.py", "abc123").startswith(
        "https://git.internal/"
    )


@pytest.mark.offline
def test_an_ssh_port_does_not_leak_into_the_web_host():
    url = browse_url("ssh://git@git.internal:2222/org/weave.git", "a.py", "abc123")
    assert url == "https://git.internal/org/weave/blob/abc123/a.py"


# ── registration ─────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_a_layout_needs_somewhere_to_point(registry):
    with pytest.raises(ProjectLayoutError):
        registry.register("alpha", "orphan")


@pytest.mark.offline
@pytest.mark.parametrize("name", ["../etc", "a/b", "", "with space"])
def test_an_unusable_repository_name_is_refused(registry, name):
    """The name becomes a record id and reaches a filesystem read through the
    registered checkout."""
    with pytest.raises(ProjectLayoutError):
        registry.register("alpha", name, clone_url="https://example.com/x")


@pytest.mark.offline
def test_resolving_an_unregistered_repository_raises_not_registered(registry):
    with pytest.raises(NotRegistered):
        registry.resolve("alpha", Locator("unknown", "a.py", "abc123"))


@pytest.mark.offline
def test_a_layout_registered_for_humans_only_links_but_does_not_claim_content():
    """`clone_url` with no checkout: we can build a link, and we must not assert
    the file exists — `exists` is the resolver check's input and a guess there
    would corrupt the count."""
    registry = ProjectLayoutRegistry(InMemoryProjectLayoutStore())
    registry.register("alpha", "weave", clone_url="https://github.com/example/weave")

    result = registry.resolve("alpha", Locator("weave", "auth.py", "abc123"))
    assert result["url"].endswith("/blob/abc123/auth.py")
    assert result["exists"] is False
    assert "content" not in result
    assert "no server-side checkout" in result["reason"]


@pytest.mark.offline
def test_the_default_revision_is_used_when_a_locator_omits_one(repo):
    """A locator cannot be built without a rev, but the endpoint may be called
    without one, so the layout's default fills in and is reported back."""
    root, _ = repo
    registry = ProjectLayoutRegistry(InMemoryProjectLayoutStore())
    registry.register("alpha", "weave", local_path=str(root), default_rev="HEAD")

    layout = registry.require("alpha", "weave")
    result = registry.resolve(
        "alpha", Locator("weave", "server/auth.py", layout.default_rev)
    )
    assert result["rev"] == "HEAD" and result["exists"] is True
