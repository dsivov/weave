"""`weave doctor` — is this machine's seat sound, and is anything metered nearby?

The question an operator actually has on a clean machine is *"why is nothing
happening"*, and the answers cluster: no subscription seat, a seat that has
expired, or a metered variable exported in the shell that the worker will refuse
to start alongside. Each produces a different symptom and none of them says which
it is.

So this reports the two things A13 turns on, side by side:

* the **seat** — is `claude` logged in, on a first-party subscription;
* the **environment** — is any API / Bedrock / Vertex variable set here.

**A metered variable present is reported, not scrubbed.** `weave doctor` diagnoses;
it does not quietly change the machine it is diagnosing. The worker's own
preflight is what enforces the boundary, and it refuses rather than repairs for
the same reason — an operator who exported `ANTHROPIC_API_KEY` believes it is
being used, and silently ignoring it would make the bill the only way to find out
otherwise.

`CLAUDE_CODE_OAUTH_TOKEN` is reported as **the seat**, never as a problem.
Scrubbing it would remove the subscription rather than protect it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from typing import Any, Dict, List, Optional

from weave.team.worker import SUBSCRIPTION_SCRUB_VARS

SEAT_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

#: Variables that force a metered backend outright, rather than merely supplying
#: a credential. Reported separately because they are a deployment *decision*.
EXPLICIT_METERED_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def register(groups) -> None:
    parser = groups.add_parser(
        "doctor", help="check this machine's Claude seat and its environment")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.set_defaults(handler=run)


def _claude_status(env: Dict[str, str]) -> str:  # pragma: no cover - shells out
    import subprocess

    try:
        out = subprocess.run(["claude", "auth", "status", "--json"], env=env,
                             capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return ""
    return f"{out.stdout}\n{out.stderr}"


def diagnose(env: Optional[Dict[str, str]] = None, status_fn=None) -> Dict[str, Any]:
    """The whole check, as data. Pure enough to test without a machine."""
    env = dict(os.environ if env is None else env)
    status_fn = status_fn or _claude_status

    metered = sorted(v for v in SUBSCRIPTION_SCRUB_VARS if env.get(v))
    explicit = sorted(f for f in EXPLICIT_METERED_FLAGS if env.get(f))
    claude_on_path = shutil.which("claude") is not None

    report: Dict[str, Any] = {
        "claude_installed": claude_on_path,
        "seat_token_exported": bool(env.get(SEAT_TOKEN_VAR)),
        "metered_variables": metered,
        "explicit_metered_backends": explicit,
        "seat": "unknown",
        "detail": "",
        "problems": [],
        "advice": [],
    }

    if not claude_on_path:
        report["seat"] = "missing"
        report["detail"] = "the `claude` CLI is not on PATH"
        report["problems"].append("claude-not-installed")
        report["advice"].append(
            "Install Claude Code on this machine — every Weave role is an "
            "ordinary Claude Code session (A10), so nothing runs without it.")
    else:
        from weave.team.worker import _parse_auth_status, scrub_api_auth

        parsed = _parse_auth_status(status_fn(scrub_api_auth(env)))
        if parsed is None:
            report["seat"] = "unknown"
            report["detail"] = "`claude auth status` did not answer in a readable form"
            report["problems"].append("seat-unreadable")
            report["advice"].append(
                "Run `claude auth status` by hand and check the output; an "
                "unreadable answer is treated as unconfirmed, never as fine.")
        elif not parsed.get("loggedIn"):
            report["seat"] = "missing"
            report["detail"] = "claude is not logged in"
            report["problems"].append("seat-missing")
            report["advice"].append("Run `claude auth login` on this machine.")
        elif str(parsed.get("subscriptionType") or "").lower() in ("", "none"):
            report["seat"] = "expired"
            report["detail"] = "logged in, but no subscription is attached"
            report["problems"].append("seat-not-a-subscription")
            report["advice"].append(
                "This seat is logged in without a subscription. Weave is "
                "subscription-only (A13) — attach a plan or use a different account.")
        else:
            report["seat"] = "ok"
            report["detail"] = (
                f"{parsed.get('subscriptionType')} via "
                f"{parsed.get('authMethod') or 'oauth'}"
                + (f" ({parsed['email']})" if parsed.get("email") else "")
            )

    if explicit:
        report["problems"].append("explicit-metered-backend")
        report["advice"].append(
            f"{', '.join(explicit)} force a metered backend. A worker will "
            "refuse to start rather than quietly ignore them — unset them.")
    if metered:
        report["problems"].append("metered-variables-present")
        report["advice"].append(
            f"{', '.join(metered)} would put Claude Code on a paid API path. "
            "They are scrubbed from what reaches `claude`, but their presence "
            "means this shell is configured for something Weave does not use.")

    report["ok"] = report["seat"] == "ok" and not explicit
    return report


def run(args: argparse.Namespace) -> int:
    report = diagnose()

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    tick = {"ok": "✓", "missing": "✗", "expired": "✗", "unknown": "?"}[report["seat"]]
    print("weave doctor\n")
    print(f"  claude installed : {'yes' if report['claude_installed'] else 'no'}")
    print(f"  subscription seat: {tick} {report['seat']}"
          + (f" — {report['detail']}" if report["detail"] else ""))
    print(f"  seat token set   : "
          f"{'yes' if report['seat_token_exported'] else 'no (using the login on this machine)'}")

    if report["explicit_metered_backends"]:
        print(f"  metered backend  : ✗ {', '.join(report['explicit_metered_backends'])}")
    if report["metered_variables"]:
        print(f"  metered vars     : ! {', '.join(report['metered_variables'])}")
    if not report["metered_variables"] and not report["explicit_metered_backends"]:
        print("  metered vars     : ✓ none")

    if report["advice"]:
        print("\nwhat to do:")
        for line in report["advice"]:
            print(f"  · {line}")
    else:
        print("\nThis machine is ready to carry Weave developers.")
    return 0 if report["ok"] else 1
