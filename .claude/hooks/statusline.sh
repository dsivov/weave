#!/usr/bin/env bash
# ONBOARDING methodology — status line (optional). Claude Code pipes session JSON on
# stdin and renders this line in the bottom bar (user-visible; NOT model context).
# Shows: methodology marker · model · context% · git branch. Degrades gracefully if
# python3 is unavailable.
input="$(cat)"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '-')"

# Pass JSON via env (not stdin) so `python3 -c` isn't fighting the script source.
line="$(SL_JSON="$input" SL_BRANCH="$branch" python3 -c '
import os, json
try:
    d = json.loads(os.environ.get("SL_JSON") or "{}")
except Exception:
    d = {}
branch = os.environ.get("SL_BRANCH", "-")
model = (d.get("model") or {}).get("display_name", "?")
cw = d.get("context_window") or {}
pct = cw.get("used_percentage")
pct = f"{int(pct)}%" if isinstance(pct, (int, float)) else "?"
print(f"⬢ methodology · {model} · ctx {pct} · {branch}")
' 2>/dev/null)"

if [ -n "$line" ]; then echo "$line"; else echo "⬢ methodology · ${branch}"; fi
