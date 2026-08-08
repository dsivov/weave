"""Container operations, behind a protocol narrow enough to fake (R71).

:class:`ContainerRuntime` is three methods — ``start`` · ``stop`` · ``running``.
Keeping it that small is what lets the reconcile loop be tested without a Docker
daemon anywhere in sight, which matters because reconcile is the part that can
quietly start a substitute for a developer someone deliberately paused.

:class:`DockerRuntime` is the real implementation. :class:`WorkerSpec` is
everything one developer container needs to exist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Protocol

from weave_core.utils import logger


class ContainerRuntime(Protocol):
    """The container operations the daemon needs. Narrow on purpose: it keeps
    Docker at arm's length so the reconcile logic is testable without one."""

    def start(self, worker_id: str, spec: "WorkerSpec") -> str: ...
    def stop(self, worker_id: str) -> None: ...
    def running(self) -> List[str]: ...


@dataclass
class WorkerSpec:
    """Everything one developer container needs to exist."""

    image: str
    server: str
    workspace: str
    workdir: str                             # absolute path of this worker's worktree
    base_branch: str = "main"
    env: Dict[str, str] = field(default_factory=dict)
    cg_token: str = ""
    test_command: List[str] = field(default_factory=list)
    # Inside a container the container is the boundary, so the agent gets the
    # full grant and runs the task to completion without anyone to ask.
    permission_mode: str = "bypassPermissions"
    # The directory holding both the shared clone and every worktree, mounted at
    # the *same absolute path* inside the container. A git worktree's `.git` is a
    # file holding `gitdir: <absolute path into the clone>`, so mounting the
    # worktree alone under some tidy `/work` leaves that pointer dangling and
    # every git command in the container fails. Identical paths inside and out
    # is what makes the checkout work at all.
    mount_root: str = ""
    # This container's Claude config directory, holding the one thing it needs:
    # the subscription credential. Mounted as CLAUDE_CONFIG_DIR.
    seat_dir: str = ""


@dataclass
class DockerRuntime:  # pragma: no cover - shells out
    """Runs each developer as a container, labelled so the daemon can find its
    own again after a restart without keeping state on disk."""

    label: str = "weave.devhost"
    host_id: str = ""
    docker: str = "docker"
    # On an SELinux host (Fedora, RHEL) a bind mount the container may read has
    # to be relabelled, or every file in it is "Permission denied" — which looks
    # exactly like a broken credential and wastes an afternoon. `:z` is shared
    # relabelling; it is accepted and ignored where SELinux is not enforcing.
    selinux_relabel: bool = True
    # How long to let a container prove it survived startup before calling the
    # start a success.
    startup_grace: float = 3.0

    def _mount(self, host_path: str, container_path: str = "") -> str:
        spec = f"{host_path}:{container_path or host_path}"
        return f"{spec}:z" if self.selinux_relabel else spec

    def _run(self, args: List[str], timeout: float = 120.0):
        import subprocess
        return subprocess.run([self.docker, *args], capture_output=True, text=True,
                              timeout=timeout)

    def start(self, worker_id: str, spec: WorkerSpec) -> str:
        mount = spec.mount_root or spec.workdir
        name = f"weave-{worker_id}"
        # Clear a previous instance rather than running with `--rm`. A container
        # that dies keeps its logs this way, which is the difference between
        # diagnosing a crashed developer and watching one silently not exist.
        self._run(["rm", "-f", name])
        args = [
            "run", "-d",
            "--name", name,
            "--label", f"{self.label}={self.host_id}",
            "--label", f"{self.label}.worker={worker_id}",
            "-v", self._mount(mount),        # same path inside, so gitdir resolves
            "-w", spec.workdir,
        ]
        if spec.seat_dir:
            args += ["-v", self._mount(spec.seat_dir),
                     "-e", f"CLAUDE_CONFIG_DIR={spec.seat_dir}"]
        for k, v in spec.env.items():
            args += ["-e", f"{k}={v}"]
        # Flags only. The image's ENTRYPOINT is already the worker module, and
        # naming it again here would append a second copy of the command line as
        # positional arguments — argparse rejects them and the container exits
        # before it prints anything the daemon can see.
        args += [
            spec.image,
            "--server", spec.server,
            "--workspace", spec.workspace,
            "--worker-id", worker_id,
            "--workdir", spec.workdir,
            "--base-branch", spec.base_branch,
            "--permission-mode", spec.permission_mode,
            "--resident",
        ]
        if spec.test_command:
            args += ["--test-cmd", " ".join(spec.test_command)]
        p = self._run(args)
        if p.returncode != 0:
            raise RuntimeError(f"docker run failed for {worker_id}: {p.stderr.strip()[:300]}")
        cid = p.stdout.strip()

        # `docker run -d` succeeds the moment the container is created, so a
        # worker that dies on startup looks like a clean start. Check that it is
        # actually alive and, if not, carry its own words into the exception —
        # otherwise the machine reports "started" for something that never ran.
        time.sleep(self.startup_grace)
        alive = self._run(["inspect", name, "--format", "{{.State.Running}}"])
        if alive.stdout.strip() != "true":
            logs = self._run(["logs", "--tail", "20", name])
            detail = (logs.stdout or logs.stderr or "").strip()[-400:]
            raise RuntimeError(f"{worker_id} exited immediately: {detail}")
        return cid

    def stop(self, worker_id: str) -> None:
        self._run(["rm", "-f", f"weave-{worker_id}"])

    def running(self) -> List[str]:
        p = self._run(["ps", "--filter", f"label={self.label}={self.host_id}",
                       "--format", "{{.Label \"" + self.label + ".worker\"}}"])
        if p.returncode != 0:
            logger.warning(f"docker ps failed: {p.stderr.strip()[:200]}")
            return []
        return sorted(w for w in (line.strip() for line in p.stdout.splitlines()) if w)
