"""The autonomous developer loop — a headless Claude Code worker (P3 · M3).

A long-running daemon that drains a Weave queue: register → heartbeat → wait for
ready work → claim → orient on the brief → build & test in a git branch → open a
PR → record the why → repeat, until the queue empties or a supervisor stops it.
It is an **outbound MCP/REST client** of Weave — control and progress ride on Weave
state, so it needs only outbound connectivity and works across NAT.

Two boundaries make it subscription-safe (RFC D9) and testable:

* **The token boundary.** :func:`preflight_subscription_auth` scrubs every
  API / Bedrock / Vertex variable and asserts ``claude /status`` reports
  subscription auth, else it refuses to start. The subscription token reaches
  only the official ``claude`` binary; the Weave client authenticates as the
  worker's own developer identity — never the subscription token.
* **The seams.** The reasoning step (``claude -p``) and the git step are
  injected (:class:`CodeRunner`, :class:`Git`), so :func:`run_worker` — the loop
  itself — is deterministic and unit-tested with fakes. The shipped shell
  implementations are thin wrappers.
"""

from __future__ import annotations

import json
import os
import shlex
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from weave.server.workspace_pool import WORKSPACE_HEADER

try:                                  # the server's logger, when the server is here
    from weave_core.utils import logger
except ImportError:                   # the developer container, which is not
    # This module is the one piece of Weave that runs inside a bare container —
    # Claude Code, git, and the standard library. `weave_core` lives on the server
    # side and pulls httpx and friends, so its absence must be survivable rather
    # than fatal; without this the container cannot start the worker at all.
    import logging

    logger = logging.getLogger("weave.worker")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(levelname)s: %(message)s")

# ── the token boundary (D9) ─────────────────────────────────────────────────
# Anything that would push Claude Code onto a paid API path. Scrubbed from the
# environment the loop hands to `claude`; their presence at preflight is refused.
SUBSCRIPTION_SCRUB_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_REGION", "ANTHROPIC_BEDROCK_BASE_URL",
    "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_VERTEX_BASE_URL",
)
# markers in `claude /status` that mean a non-subscription path — refuse on sight.
_NON_SUBSCRIPTION_MARKERS = ("api key", "bedrock", "vertex")
# markers that confirm subscription (Claude Pro / Max) auth.
_SUBSCRIPTION_MARKERS = ("pro", "max", "subscription", "claude account", "oauth")


class SubscriptionAuthError(RuntimeError):
    """Preflight refused: Claude Code is not on subscription auth."""


def scrub_api_auth(env: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of *env* with every API/Bedrock/Vertex variable removed."""
    return {k: v for k, v in env.items() if k not in SUBSCRIPTION_SCRUB_VARS}


def _claude_status(env: Dict[str, str]) -> str:  # pragma: no cover - shells out
    """`claude auth status --json` — the non-interactive auth probe.

    (`/status` is an in-session slash command and is unavailable to a headless
    worker, which is exactly the environment this runs in.)
    """
    import subprocess

    out = subprocess.run(["claude", "auth", "status", "--json"], env=env,
                         capture_output=True, text=True, timeout=30)
    return f"{out.stdout}\n{out.stderr}"


def preflight_subscription_auth(
    *, env: Optional[Dict[str, str]] = None,
    status_fn: Optional[Callable[[Dict[str, str]], str]] = None,
) -> Dict[str, str]:
    """Assert the subscription token boundary, returning the scrubbed env the loop
    must hand to ``claude``. Refuses if any API/Bedrock/Vertex path is configured
    or if ``claude auth status`` doesn't confirm subscription auth."""
    env = dict(os.environ if env is None else env)
    for flag in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        if env.get(flag):
            raise SubscriptionAuthError(
                f"{flag} is set — the autonomous worker is subscription-only (D9)")
    scrubbed = scrub_api_auth(env)
    status = (status_fn or _claude_status)(scrubbed)

    parsed = _parse_auth_status(status)
    if parsed is not None:
        _assert_subscription(parsed, status)
    else:
        # Older CLIs (and fakes) report free text — fall back to marker matching.
        low = status.lower()
        if any(m in low for m in _NON_SUBSCRIPTION_MARKERS):
            raise SubscriptionAuthError(f"claude is not on subscription auth: {status.strip()}")
        if not any(m in low for m in _SUBSCRIPTION_MARKERS):
            raise SubscriptionAuthError(
                f"could not confirm subscription auth from claude auth status: {status.strip()}")
    logger.info("Weave worker: subscription auth confirmed; API/Bedrock/Vertex scrubbed")
    return scrubbed


def _parse_auth_status(status: str) -> Optional[Dict[str, Any]]:
    """The JSON object from `claude auth status --json`, or None if this isn't it."""
    from weave_core.jsonio import _extract_json_object

    payload = _extract_json_object(status)
    return payload if isinstance(payload, dict) and "loggedIn" in payload else None


def _assert_subscription(auth: Dict[str, Any], raw: str) -> None:
    """Refuse anything that isn't a logged-in first-party subscription seat."""
    if not auth.get("loggedIn"):
        raise SubscriptionAuthError(
            "claude is not logged in — run `claude auth login` on this machine "
            "to attach a subscription seat (D9)")
    provider = str(auth.get("apiProvider") or "").lower()
    if provider and provider != "firstparty":
        raise SubscriptionAuthError(
            f"claude is on the '{provider}' provider — the autonomous worker is "
            "subscription-only (D9)")
    method = str(auth.get("authMethod") or "").lower()
    if "api" in method and "key" in method:
        raise SubscriptionAuthError(
            f"claude is authenticated by API key ({method}) — subscription-only (D9)")
    subscription = str(auth.get("subscriptionType") or "").lower()
    if subscription in ("", "none"):
        raise SubscriptionAuthError(
            f"no Claude subscription on this seat: {raw.strip()}")
    logger.info(
        f"Weave worker: subscription seat '{subscription}' via {method or 'oauth'} "
        f"({auth.get('email') or 'unknown account'})")


# ── the build seams (injected so the loop is deterministic to test) ──────────

@dataclass
class RunResult:
    ok: bool = True
    summary: str = ""
    commit_sha: Optional[str] = None
    pr_url: str = ""


class CodeRunner(Protocol):
    def __call__(self, brief: Dict[str, Any]) -> RunResult: ...


class Git(Protocol):
    def new_branch(self, branch: str) -> None: ...
    def run_tests(self) -> bool: ...


class ClaimConflict(Exception):
    """The claim was lost to another worker (HTTP 409) — try the next task."""


# ── the Weave client (outbound REST; token-less-to-subscription) ────────────────

class WeaveClient:
    """Thin HTTP client for the /weave surface. Authenticates as the worker's own
    identity (never the subscription token). Stdlib-only so it runs in a bare
    container."""

    def __init__(self, base_url: str, workspace: str, *, token: Optional[str] = None,
                 api_key: Optional[str] = None, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._ws = workspace
        self._token = token
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", WORKSPACE_HEADER: self._ws}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:  # pragma: no cover - network
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self._base}{path}", data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 409:
                raise ClaimConflict(e.read().decode())
            raise

    # the surface the loop uses (all network → excluded from unit coverage)
    def register(self, worker_id, *, role="developer", host="", goal=""):  # pragma: no cover
        return self._call("POST", "/weave/workers/register",
                          {"worker": worker_id, "host": host, "goal": goal})

    def heartbeat(self, worker_id, *, current_task=None):  # pragma: no cover
        return self._call("POST", f"/weave/workers/{worker_id}/heartbeat",
                          {"current_task": current_task})

    def wait_for_ready(self, timeout=25.0):  # pragma: no cover
        return self._call("GET", f"/weave/tasks/wait?timeout={timeout}").get("ready", [])

    def claim(self, task_id, worker_id):  # pragma: no cover
        return self._call("POST", f"/weave/tasks/{task_id}/claim", {"worker": worker_id})

    def brief(self, task_id):  # pragma: no cover
        return self._call("GET", f"/weave/tasks/{task_id}/brief")

    def record_commit(self, task_id, *, sha, subject=""):  # pragma: no cover
        return self._call("POST", f"/weave/tasks/{task_id}/commit",
                          {"sha": sha, "subject": subject})

    def open_pull_request(self, task_id, *, branch="", url=""):  # pragma: no cover
        return self._call("POST", f"/weave/tasks/{task_id}/pull-request",
                          {"branch": branch, "url": url})

    def record_decision(self, *, src, tgt, relation, decision_trace):  # pragma: no cover
        return self._call("POST", "/weave/decisions",
                          {"src": src, "tgt": tgt, "relation": relation,
                           "decision_trace": decision_trace})

    def record_learning(self, insight, *, task_id=None):  # pragma: no cover
        return self._call("POST", "/weave/learnings", {"insight": insight, "task": task_id})

    # -- the dev-host surface (P8) — used by the daemon, not by a worker -------

    def register_host(self, host_id, *, machine="", repo="", base_branch="main",
                      image="", seat="unknown", seat_detail="",
                      capabilities=None, version=""):  # pragma: no cover
        return self._call("POST", "/weave/hosts/register", {
            "host": host_id, "machine": machine, "repo": repo,
            "base_branch": base_branch, "image": image, "seat": seat,
            "seat_detail": seat_detail, "capabilities": list(capabilities or []),
            "version": version})

    def heartbeat_host(self, host_id, *, workers=None, seat=None,
                       seat_detail=None):  # pragma: no cover
        """Returns the machine's marching orders: control-state and how many
        developers the team wants running here."""
        return self._call("POST", f"/weave/hosts/{host_id}/heartbeat", {
            "workers": list(workers or []), "seat": seat, "seat_detail": seat_detail})


# ── the loop ─────────────────────────────────────────────────────────────────

def run_worker(
    client: Any, *, worker_id: str, code_runner: CodeRunner, git: Git,
    role: str = "developer", host: str = "", goal: str = "",
    poll_timeout: float = 25.0, idle_rounds_before_exit: int = 1,
    max_iterations: int = 10_000, sleep: Callable[[float], None] = time.sleep,
    pause_backoff: float = 2.0,
) -> Dict[str, Any]:
    """Drain the queue as an autonomous developer. Deterministic given the seams;
    ``client`` is any object exposing the WeaveClient surface (a fake in tests).

    Control-state is honoured between steps: ``pause`` idles, ``stop`` halts
    cleanly. The loop exits when the ready-set stays empty for
    ``idle_rounds_before_exit`` rounds (queue drained / goal met). It opens one
    PR per task and never merges.
    """
    client.register(worker_id, role=role, host=host, goal=goal)
    completed: List[str] = []
    idle = 0

    def stopped() -> bool:
        return client.heartbeat(worker_id).get("control") == "stop"

    for _ in range(max_iterations):
        ctl = client.heartbeat(worker_id).get("control", "run")
        if ctl == "stop":
            break
        if ctl == "pause":
            sleep(pause_backoff)
            continue

        ready = client.wait_for_ready(poll_timeout) or []
        if not ready:
            idle += 1
            if idle >= idle_rounds_before_exit:
                break                                   # queue drained → goal met
            continue
        idle = 0

        task = ready[0]
        tid = task["id"]
        try:
            client.claim(tid, worker_id)
        except ClaimConflict:
            continue                                    # lost the race; next task
        client.heartbeat(worker_id, current_task=tid)

        brief = client.brief(tid)
        branch = f"weave/{tid}"
        git.new_branch(branch)
        result = code_runner(brief)                     # the `claude -p` step
        if stopped():                                   # supervisor halt mid-task
            break

        if result.commit_sha:
            client.record_commit(tid, sha=result.commit_sha, subject=result.summary)

        if not (result.ok and git.run_tests()):
            client.record_learning(
                f"task {tid} did not pass: {result.summary or 'tests failed'}", task_id=tid)
            continue                                    # left for review / re-work

        client.open_pull_request(tid, branch=branch, url=result.pr_url)
        client.record_decision(
            src=worker_id, tgt=tid, relation="implemented",
            decision_trace=result.summary or f"implemented {tid}")
        completed.append(tid)
        logger.info(f"Weave worker '{worker_id}' completed '{tid}' → PR opened")

    client.heartbeat(worker_id)
    return {"worker": worker_id, "completed": completed, "count": len(completed)}


# ── shell implementations + CLI entrypoint (thin; not unit-covered) ──────────

@dataclass
class ShellGit:  # pragma: no cover - trivial shell-outs; new_branch is covered
    workdir: str = "."
    test_cmd: List[str] = field(default_factory=lambda: ["python", "-m", "pytest", "-q"])
    env: Dict[str, str] = field(default_factory=dict)
    base_branch: str = "main"

    def _run(self, args):
        import subprocess
        return subprocess.run(args, cwd=self.workdir, env={**os.environ, **self.env},
                             capture_output=True, text=True)

    def new_branch(self, branch: str) -> None:
        """Cut a task branch from the shared base, never from wherever HEAD sat.

        Branching off the previous task's branch makes every PR carry the last
        task's commits too, so a reviewer can no longer see what one task
        changed. The worktree belongs to this worker alone, so leftovers from an
        abandoned run are discarded rather than riding along into the next PR.
        """
        self._run(["git", "checkout", "-B", branch, self.base_branch])
        self._run(["git", "reset", "--hard", self.base_branch])
        self._run(["git", "clean", "-fd"])

    def run_tests(self) -> bool:
        return self._run(self.test_cmd).returncode == 0


@dataclass
class ShellClaudeRunner:  # pragma: no cover - shells out
    """Runs ``claude -p`` headless with the subscription-scrubbed env, then commits.

    Two things this must get right, and both are easy to get wrong:

    * **The brief is not a prompt.** Handing `claude` a raw JSON dump produces a
      description of the work, not the work. :func:`build_prompt` turns the brief
      into an instruction with the task, its acceptance criteria, and precedent.
    * **Headless sessions cannot edit by default.** Without an explicit tool
      allowance the run reads the repo, decides what it *would* do, and changes
      nothing — leaving a branch with no commit.

    **How much permission depends on what is containing the run**, which is why
    it is a parameter and not a constant:

    * On a bare host (a developer's own checkout) the grant is ``acceptEdits``
      plus an allow-list. The blast radius is a real machine, so the run may
      edit files and run tests but not arbitrary commands.
    * Inside a dev-host container the grant is ``bypassPermissions`` with no
      allow-list. The container is the boundary — it holds one throwaway
      worktree and no credentials — so narrowing tools again inside it buys no
      safety and costs the agent the ability to finish (installing a dependency,
      running a build, using a tool nobody predicted). A task runs to completion
      with full write permission, and the isolation is the container's job.

    A run that produces no commit is a **failure**, not an empty PR: the loop
    records the learning instead of asking a human to review nothing.
    """
    workdir: str = "."
    env: Dict[str, str] = field(default_factory=dict)
    permission_mode: str = "acceptEdits"
    allowed_tools: List[str] = field(
        default_factory=lambda: ["Read", "Edit", "Write", "Glob", "Grep",
                                 "Bash(git *)", "Bash(python *)", "Bash(pytest*)"])
    timeout: float = 1800.0

    def __call__(self, brief: Dict[str, Any]) -> RunResult:
        import subprocess

        task_id = (brief.get("task") or {}).get("id", "")
        env = {**scrub_api_auth(dict(os.environ)), **self.env}
        cmd = ["claude", "-p", build_prompt(brief),
               "--permission-mode", self.permission_mode]
        # An allow-list on top of `bypassPermissions` would contradict it: the
        # container grant is deliberately total, so the task can finish whatever
        # it needs to do without a human to ask.
        if self.allowed_tools and self.permission_mode != "bypassPermissions":
            cmd += ["--allowedTools", *self.allowed_tools]
        try:
            proc = subprocess.run(cmd, cwd=self.workdir, env=env, capture_output=True,
                                  text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return RunResult(ok=False, summary=f"claude timed out after {self.timeout:.0f}s")
        if proc.returncode != 0:
            return RunResult(ok=False, summary=(proc.stderr or proc.stdout)[:400])

        summary = (proc.stdout or "").strip()[:400]
        subprocess.run(["git", "add", "-A"], cwd=self.workdir,
                       capture_output=True, text=True)
        c = subprocess.run(["git", "commit", "-m", f"weave: {task_id}"],
                           cwd=self.workdir, capture_output=True, text=True)
        if c.returncode != 0:
            # Nothing staged — the session changed no files. Don't open a PR on it.
            return RunResult(
                ok=False,
                summary=f"the session produced no change to commit. {summary}"[:400])
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workdir,
                             capture_output=True, text=True).stdout.strip()
        return RunResult(ok=True, summary=summary, commit_sha=sha)


def build_prompt(brief: Dict[str, Any]) -> str:
    """Turn a coordinator brief into the instruction an autonomous developer runs.

    Kept a module-level function so it is testable without shelling out.
    """
    task = brief.get("task") or {}
    parts = [
        "You are an autonomous developer on a Weave team. Implement exactly one "
        "task in this repository, then stop.",
        f"\n## Task {task.get('id', '')}\n{task.get('title', '')}",
    ]
    if task.get("description"):
        parts.append(f"\n{task['description']}")
    if brief.get("change_request"):
        parts.append(f"\n## Change request\n{brief['change_request']}")
    if brief.get("touches"):
        parts.append("\n## Modules you may touch\n"
                     + ", ".join(f"`{t}`" for t in brief["touches"])
                     + "\nStay inside them — another agent may be working elsewhere.")
    deps = [d for d in (brief.get("depends_on") or [])]
    if deps:
        parts.append("\n## Depends on\n"
                     + "\n".join(f"- {d.get('id')} ({d.get('status')})" for d in deps))
    precedent = brief.get("precedent") or []
    if precedent:
        lines = []
        for p in precedent[:3]:
            if isinstance(p, dict):
                lines.append(f"- {p.get('decision_trace') or p.get('summary') or p}")
            else:
                lines.append(f"- {p}")
        parts.append("\n## Precedent — how similar work was decided before\n"
                     + "\n".join(lines))
    parts.append(
        "\n## How to work\n"
        "1. Read the surrounding code first and match its conventions — reuse what "
        "exists rather than inventing a parallel way of doing the same thing.\n"
        "2. Make the change, and add or update tests that cover it.\n"
        "3. Run the test suite and make it pass before you finish.\n"
        "4. Do NOT commit, branch, push, or merge — the worker handles git.\n"
        "5. Finish with a two-or-three sentence summary of what you changed and why."
    )
    return "\n".join(parts)


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - entrypoint
    import argparse
    import socket

    ap = argparse.ArgumentParser(description="Weave autonomous developer worker")
    ap.add_argument("--server", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--worker-id", required=True)
    ap.add_argument("--goal", default="")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--token", default=os.environ.get("WEAVE_SERVER_TOKEN"))
    ap.add_argument("--api-key", default=os.environ.get("WEAVE_SERVER_API_KEY"))
    ap.add_argument("--poll-timeout", type=float, default=25.0,
                    help="Seconds to long-poll the ready-set before re-checking control state.")
    ap.add_argument("--idle-rounds", type=int, default=1,
                    help="Empty ready-sets tolerated before the worker exits. Use a large "
                         "value (or --resident) to keep it waiting for work like a daemon.")
    ap.add_argument("--resident", action="store_true",
                    help="Stay up waiting for work indefinitely; stop it from the Weave board.")
    ap.add_argument("--base-branch", default="main",
                    help="Branch every task branches from, so one PR shows one task's work.")
    ap.add_argument("--permission-mode", default="acceptEdits",
                    choices=("acceptEdits", "bypassPermissions"),
                    help="How much the agent may do. Use bypassPermissions ONLY where a "
                         "container is the boundary — a dev-host worker does.")
    ap.add_argument("--test-cmd", default="",
                    help="Command that verifies the work (shell-split); defaults to pytest -q.")
    args = ap.parse_args(argv)

    # D9: assert the token boundary before anything else.
    scrubbed = preflight_subscription_auth()
    client = WeaveClient(args.server, args.workspace, token=args.token, api_key=args.api_key)
    result = run_worker(
        client, worker_id=args.worker_id, host=socket.gethostname(), goal=args.goal,
        code_runner=ShellClaudeRunner(workdir=args.workdir, env=scrubbed,
                                      permission_mode=args.permission_mode),
        git=ShellGit(workdir=args.workdir, env=scrubbed, base_branch=args.base_branch,
                     **({"test_cmd": shlex.split(args.test_cmd)} if args.test_cmd else {})),
        poll_timeout=args.poll_timeout,
        idle_rounds_before_exit=(2**31 if args.resident else args.idle_rounds))
    logger.info(f"Weave worker finished: {result}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
