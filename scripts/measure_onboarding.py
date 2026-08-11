#!/usr/bin/env python
"""Time the published onboarding steps, clean machine → first governed action (R2).

The M6 gate is *a clean machine reaching a live fleet **by the published steps
only***, with the onboarding timed. So this harness runs the steps from
`docs/guides/first-fleet.md` and stamps each one, rather than timing an idealised
path a reader does not have.

    python scripts/measure_onboarding.py --working-dir /tmp/onboard --json

**On a baseline (AS7).** R2 wants a comparison, and there is no honest one here:
the thing Weave replaces is "a person wires up agents by hand", which has no
reproducible clock. Rather than invent a number to beat, this publishes **the
Weave figure alone** and says so in its own output. An unlabelled comparison
would be worth less than no comparison.

**What is and is not measured.** Every step that a command can perform is timed.
Two steps in the guide are deliberately *not* automatable and are reported as
**manual**, with their own clock left out of the total rather than estimated:
signing the team vocabulary (a human reads a diff and signs it — the point of the
wizard is that a person decides) and `claude auth login` (an interactive browser
flow). Counting a guess for either would make the total say something untrue.

Exit codes: **0** every automated step succeeded · **1** a step failed ·
**2** could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

PYTHON = sys.executable


class Step:
    """One documented step, with the clock around it."""

    def __init__(self, name: str, guide_step: str, argv: Optional[List[str]] = None,
                 *, manual: str = "", env: Optional[Dict[str, str]] = None,
                 allow_failure: bool = False) -> None:
        self.name = name
        self.guide_step = guide_step
        self.argv = argv
        self.manual = manual
        self.env = env or {}
        self.allow_failure = allow_failure

    def run(self, cwd: str) -> Dict[str, Any]:
        if self.manual:
            return {"step": self.name, "guide": self.guide_step, "manual": True,
                    "reason": self.manual, "seconds": None, "ok": True}

        started = time.perf_counter()
        completed = subprocess.run(
            self.argv, cwd=cwd, capture_output=True, text=True,
            env={**os.environ, **self.env}, timeout=300,
        )
        elapsed = time.perf_counter() - started
        ok = completed.returncode == 0 or self.allow_failure
        return {
            "step": self.name,
            "guide": self.guide_step,
            "manual": False,
            "seconds": round(elapsed, 3),
            "returncode": completed.returncode,
            "ok": ok,
            "stderr": (completed.stderr or "").strip()[-400:] if not ok else "",
        }


def _steps(working_dir: str, token_secret: str) -> List[Step]:
    """The guide's steps, in the order it gives them.

    **Five of these were manual when this harness was written**, and three became
    commands in P6 — signing the vocabulary, registering the project, and scaling
    a machine's developers. That is most of the change in the number this prints,
    and it is worth saying: the figure did not drop because the same work got
    faster, it dropped because work that used to be done in a browser is now a
    line an operator can copy.
    """
    env = {"WEAVE_WORKING_DIR": working_dir}
    return [
        Step("check the machine", "1 · Check the machine before you configure it",
             [PYTHON, "-m", "weave.cli", "doctor"],
             # A machine with no seat is a real answer, not a harness failure —
             # the number still means "how long did the published step take".
             allow_failure=True),
        Step("initialise storage and secret", "2 · Start the server",
             [PYTHON, "-m", "weave.cli", "init", "--working-dir", working_dir]),
        Step("start the server", "2 · Start the server",
             manual="`weave up` runs in the foreground until Ctrl-C. Timing it "
                    "means timing 'until the first request succeeds', which is a "
                    "different measurement from the steps around it and is "
                    "reported separately rather than folded into their total"),
        Step("create the first administrator", "3 · Create the first administrator",
             [PYTHON, "-m", "weave.cli", "user", "add", "onboard-admin",
              "--role", "admin", "--workspaces", "team",
              "--password", "a-good-password-123"],
             env=env),
        Step("sign the team vocabulary", "4 · Give the workspace its vocabulary",
             [PYTHON, "-m", "weave.cli", "roles", "install",
              "--working-dir", working_dir, "--workspace", "team",
              "--approver", "onboard-admin"]),
        Step("register the project", "5 · Tell the workspace what it is building",
             [PYTHON, "-m", "weave.cli", "project", "register",
              "--working-dir", working_dir, "--workspace", "team",
              "--repo", "https://example.invalid/acme/thing.git",
              "--test-command", "python3 -m pytest -q"]),
        Step("attach a machine", "6 · Attach a machine that carries developers",
             manual="`claude auth login` is an interactive browser flow, and the "
                    "daemon runs on a *second* machine — a one-box harness cannot "
                    "time a step whose point is that it happens somewhere else"),
        Step("put developers to work", "7 · Put developers to work",
             manual="`weave agents up` needs a registered dev host, which needs "
                    "the machine from step 6. Timing it against a host this "
                    "harness registered itself would measure the harness"),
        Step("migrate existing reviews", "8 · If you are upgrading an existing install",
             [PYTHON, "-m", "weave.cli", "migrate", "reviews",
              "--workspace", "team", "--working-dir", working_dir, "--dry-run"]),
    ]


def _run(args: argparse.Namespace) -> int:
    working_dir = os.path.abspath(args.working_dir)
    if args.fresh and os.path.isdir(working_dir):
        # Only ever a directory this harness was told to own. Never a path it
        # did not create — build output and someone else's storage live under
        # paths that look just like this one.
        if not args.i_created_this:
            print(f"refusing to clear {working_dir}: pass --i-created-this to "
                  "confirm this directory is the harness's to delete",
                  file=sys.stderr)
            return 2
        shutil.rmtree(working_dir)
    os.makedirs(working_dir, exist_ok=True)

    token_secret = os.environ.get("WEAVE_TOKEN_SECRET", "onboarding-harness-secret-x" * 2)
    results = [step.run(os.getcwd()) for step in _steps(working_dir, token_secret)]

    timed = [r for r in results if not r["manual"]]
    total = round(sum(r["seconds"] for r in timed), 3)
    failed = [r for r in results if not r["ok"]]

    report = {
        "working_dir": working_dir,
        "steps": results,
        "automated_steps": len(timed),
        "manual_steps": len(results) - len(timed),
        "total_seconds": total,
        "failed": [r["step"] for r in failed],
        "baseline": None,
        "baseline_note": (
            "No baseline is published (AS7). The alternative Weave replaces — "
            "wiring up agents by hand — has no reproducible clock, so this is "
            "the Weave figure alone rather than a comparison against an invented "
            "number."
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("onboarding — the published steps, timed\n")
        for r in results:
            if r["manual"]:
                print(f"  {'manual':>8}   {r['step']}")
                print(f"             ({r['reason']})")
            else:
                mark = "✓" if r["ok"] else "✗"
                print(f"  {r['seconds']:>7.2f}s {mark} {r['step']}")
                if not r["ok"] and r["stderr"]:
                    print(f"             {r['stderr'].splitlines()[-1][:120]}")
        print(f"\n  total (automated steps): {total:.2f}s "
              f"over {len(timed)} step(s); {report['manual_steps']} manual")
        print(f"\n  {report['baseline_note']}")

    if failed:
        print(f"\n{len(failed)} step(s) failed: {', '.join(report['failed'])}",
              file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/measure_onboarding.py",
        description="Time the published onboarding steps (R2, M6 gate).",
    )
    parser.add_argument("--working-dir", default="./onboarding_measure")
    parser.add_argument("--fresh", action="store_true",
                        help="clear the working directory first")
    parser.add_argument("--i-created-this", action="store_true",
                        help="confirm --fresh may delete the working directory")
    parser.add_argument("--json", action="store_true")
    return _run(parser.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
