#!/usr/bin/env bash
# nameguard.sh — the rebrand is enforced, not remembered (A3, R2, R3, D-004, D-014).
#
# Fails the build on any occurrence of the source product's two names — any case,
# any separator — in a filename, module path, environment variable, header,
# storage identifier, log string, UI string or document.
#
# THE SOLE EXEMPTION (D-014): a `docs/BLOG_*.html` file carrying the marker
#
#     <!-- nameguard:allow lineage -->
#
# The vision piece tells the project's origin, and the concrete lineage is what
# makes its argument land. The exemption is narrow, marked and greppable, so it
# cannot quietly widen — and every honoured marker is REPORTED on each run (R3a),
# because an exemption nobody sees is an exemption that spreads. Adding a second
# one is a contract amendment, not a commit.
#
# Note this script contains no literal forbidden token: the pattern is assembled
# from fragments below. That is not cleverness for its own sake — a guard that
# had to spell what it forbids would flag itself, and the only fixes for that are
# a second exemption (barred) or excluding the guard from its own check (worse:
# the one file nobody would be watching).
#
#   scripts/nameguard.sh          # scan; non-zero exit on any hit
#   scripts/nameguard.sh --list   # show the honoured exemptions and stop

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── the forbidden pattern, assembled so it never appears whole ───────────────
A="light"; A="${A}rag"                       # the engine's product name
B="context"; C="graph"                       # the platform's product name
PATTERN="${A}|${B}[ _-]?${C}"

MARKER="nameguard:"          # completed below, for the same reason
MARKER="${MARKER}allow lineage"

# ── what gets scanned ────────────────────────────────────────────────────────
# Everything the repository ships: tracked files AND untracked files that are not
# ignored. Both halves matter — scanning only tracked files would let a freshly
# added module pass the guard right up until someone stages it, which is exactly
# when a pre-commit hook is supposed to catch it.
list_files() {
  if git rev-parse --git-dir >/dev/null 2>&1; then
    { git ls-files -z; git ls-files -z --others --exclude-standard; }
  else
    find . -type f -not -path './.git/*' -print0
  fi \
    | tr '\0' '\n' \
    | grep -vE '^(\.git/|node_modules/|.*/node_modules/|weave-ui/dist/)' \
    | grep -vE '(^|/)__pycache__/' \
    | grep -vE '\.(png|jpg|jpeg|gif|ico|woff2?|ttf|eot|pdf|zip|gz)$'
}

# ── out of scope (A3, contract v3 / D-027) ───────────────────────────────────
# The seven pipeline artifacts that STATE this rule and TRACE the fork. A3 bans
# the tokens in product documentation; these are not product documentation, and
# they cannot say what is banned without naming it — A3's own text quotes both
# tokens, and the work plan's source→destination rows are made of them.
#
# ENUMERATED, never a pattern. "docs/*.md" would quietly swallow every guide
# written from here on, which is precisely the documentation A3 exists to
# protect. An eighth file is a contract amendment, not a commit.
PIPELINE_ARTIFACTS=(
  "docs/CONSTRAINTS.md"
  "docs/DECISIONS.md"
  "docs/WEAVE_RFC.html"
  "docs/WEAVE_DRP.md"
  "docs/WEAVE_WORK_PLAN.md"
  "docs/DOCS_INDEX.md"
  "docs/START.md"
)

is_pipeline_artifact() {
  local f="$1" a
  for a in "${PIPELINE_ARTIFACTS[@]}"; do
    [[ "$f" == "$a" ]] && return 0
  done
  return 1
}

# ── exemptions ───────────────────────────────────────────────────────────────
is_exempt() {
  local f="$1"
  case "$f" in
    docs/BLOG_*.html) grep -qF -- "$MARKER" "$f" 2>/dev/null && return 0 ;;
  esac
  return 1
}

skipped=()
honoured=()
violations=0
files_with_hits=0

while IFS= read -r f; do
  [[ -f "$f" ]] || continue

  if is_pipeline_artifact "$f"; then
    skipped+=("$f")
    continue
  fi

  if is_exempt "$f"; then
    honoured+=("$f")
    continue
  fi

  # 1. the path itself — a rebrand that stops at file contents is half a rebrand
  if printf '%s' "$f" | grep -qiE -- "$PATTERN"; then
    echo "PATH     $f"
    violations=$((violations + 1))
  fi

  # 2. the contents
  if hits="$(grep -inE -- "$PATTERN" "$f" 2>/dev/null)"; then
    files_with_hits=$((files_with_hits + 1))
    n="$(printf '%s\n' "$hits" | wc -l)"
    violations=$((violations + n))
    printf '%s\n' "$hits" | head -5 | while IFS= read -r line; do
      echo "CONTENT  $f:$line"
    done
    [[ "$n" -gt 5 ]] && echo "CONTENT  $f: … and $((n - 5)) more"
  fi
done < <(list_files)

# ── report ───────────────────────────────────────────────────────────────────
# Everything skipped is announced, every run. An exemption nobody sees is an
# exemption that spreads (R3a) — and the same goes for a scope carve-out.
echo
if [[ ${#skipped[@]} -gt 0 ]]; then
  echo "nameguard: ${#skipped[@]} pipeline artifact(s) out of scope (A3 v3, D-027) —"
  echo "           these state the rule and trace the fork; they are not product docs:"
  for f in "${skipped[@]}"; do echo "  · $f"; done
fi

if [[ ${#honoured[@]} -gt 0 ]]; then
  echo "nameguard: honoured ${#honoured[@]} lineage exemption(s) — the only content exemption there is:"
  for f in "${honoured[@]}"; do echo "  · $f"; done
else
  echo "nameguard: no lineage exemptions honoured."
fi

if [[ "${1:-}" == "--list" ]]; then
  exit 0
fi

if [[ "$violations" -gt 0 ]]; then
  echo
  echo "nameguard: FAILED — $violations occurrence(s) across $files_with_hits file(s)." >&2
  echo "  The rebrand is a constraint (A3), not a preference. Rename it, or the name" >&2
  echo "  teaches the old vocabulary to everyone who reads the code next." >&2
  exit 1
fi

echo "nameguard: clean — 0 occurrences outside the marked lineage passage ✓"
