"""Host-side clone, worktrees and branch publish — why containers hold no git key.

The clone and every worktree are made **on the host**, with whatever git
credentials this machine already has; only the resulting directory is
bind-mounted into a container. So a container never holds a credential that
could push anywhere, which matters a great deal when the agent inside it runs
with full write permission (R72, A15). The blast radius stays a branch and a PR.

Publishing happens here for the same reason: the worker commits inside a
container that deliberately has no git credential, so the push has to happen out
here. Without it a task branch exists in exactly one clone on one box — and once
there is more than one machine in the fleet, no single place holds every branch,
so an integration build cannot be assembled at all.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Set

from weave_core.utils import logger


def task_branches(local: List[str], worker_ids: List[str]) -> List[str]:
    """The branches worth publishing: task work, never a worker's own scratch.

    ``_worktree_maker`` parks each worker's checkout on ``weave/<worker>-base``,
    which exists only so the worktree has somewhere to sit between tasks. It
    holds no work and means nothing to anyone else, so it stays on the machine.
    Matched by worker id rather than by a name pattern — a task legitimately
    called ``base`` should still be published.
    """
    private = {f"weave/{w}-base" for w in worker_ids}
    return [b for b in local if b not in private]


def _branch_publisher(clone_root: str):  # pragma: no cover - shells out
    """Push finished task branches to the project's origin.

    **This is the only way a task's code leaves the machine that wrote it.** The
    worker commits inside a container that deliberately holds no git credential,
    so the push has to happen out here on the host, where the machine's own
    credentials already live.

    Without it a branch exists in exactly one clone on one box: a reviewer
    cannot read it, an integrator cannot fetch it, and once there is more than
    one machine in the fleet *no single place holds every branch*, so an
    integration build cannot be assembled at all — the tasks are each complete
    and the release is still impossible.

    A rejected push is reported and never forced. Task branches are cut fresh
    from the base branch, so a non-fast-forward means something unexpected is
    already published under that name, and overwriting someone's history to
    resolve it is not this daemon's call to make.
    """
    import subprocess

    warned: Set[str] = set()

    def git(*args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", clone_root, *args],
                              capture_output=True, text=True)

    def publish(worker_ids: List[str], base: str = "main") -> List[str]:
        if not os.path.isdir(os.path.join(clone_root, ".git")):
            return []
        listed = git("for-each-ref", "--format=%(refname:short)", "refs/heads/weave/")
        pushed: List[str] = []
        for branch in task_branches(listed.stdout.split(), worker_ids):
            # A branch is cut the moment a task is claimed, well before there is
            # anything on it. Publishing that would put a branch in the shared
            # repo for every task ever started, including the ones that failed
            # and never produced a commit — so wait until it carries work.
            ahead = git("rev-list", "--count", f"{base}..{branch}").stdout.strip()
            if ahead in ("", "0"):
                continue
            here = git("rev-parse", branch).stdout.strip()
            there = git("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()
            if here and here == there:
                continue                      # already published, unchanged
            p = git("push", "origin", f"{branch}:{branch}")
            if p.returncode == 0:
                pushed.append(branch)
                warned.discard(branch)
            elif branch not in warned:
                # Once per branch: a repo this machine cannot push to would
                # otherwise repeat the same failure on every heartbeat.
                warned.add(branch)
                logger.warning(
                    f"could not publish '{branch}': {p.stderr.strip()[:200]}")
        return pushed

    return publish


def _worktree_maker(clone_root: str, worktree_root: str):  # pragma: no cover - shells out
    """Prepare one developer's checkout, cloning the project first if needed.

    The clone and the worktrees are made **on the host**, with whatever git
    credentials this machine already has, and only the resulting directory is
    bind-mounted into the container. A container therefore never holds a
    credential that could push anywhere — which matters a great deal when the
    agent inside it is running with full write permission.
    """
    import subprocess

    def run(args, cwd=None):
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)

    def prepare(worker_id: str, project: Dict[str, Any]) -> str:
        repo_url = project.get("repo") or ""
        base = project.get("base_branch") or "main"
        if not repo_url:
            raise RuntimeError("no project repository configured for this workspace")

        # The shared clone, made once per machine.
        if not os.path.isdir(os.path.join(clone_root, ".git")):
            os.makedirs(os.path.dirname(clone_root) or ".", exist_ok=True)
            p = run(["git", "clone", repo_url, clone_root])
            if p.returncode != 0:
                raise RuntimeError(f"clone failed: {p.stderr.strip()[:300]}")
        else:
            run(["git", "fetch", "origin"], cwd=clone_root)

        path = os.path.join(worktree_root, worker_id)
        if not os.path.isdir(path):
            os.makedirs(worktree_root, exist_ok=True)
            p = run(["git", "-C", clone_root, "worktree", "add", path,
                     "-b", f"weave/{worker_id}-base", base])
            if p.returncode != 0:
                raise RuntimeError(f"worktree add failed: {p.stderr.strip()[:300]}")

        setup = project.get("setup_command") or []
        if setup:
            run(list(setup), cwd=path)
        return path

    return prepare
