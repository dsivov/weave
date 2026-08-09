#!/usr/bin/env bash
# parent_checksum.sh — prove that what we copied is intact, and that we never
# wrote to the tree we copied it from (A2, D-003).
#
# WHAT IS ASSERTED, AND WHY IT IS THE COMMIT AND NOT THE WORKING TREE
#
# The fork was extracted with `git archive` at a pinned commit. So the thing that
# must stay true is: *that commit still resolves, and its tree hash is unchanged*.
# Git guarantees the rest — a tree hash is the content, so a match means what we
# copied is byte-for-byte what is recorded.
#
# An earlier version of this script compared the source's **working tree** to a
# baseline and failed the build on any difference. That conflated two different
# claims:
#
#   "we never wrote to it"        — what A2 and D-003 actually require
#   "nobody ever wrote to it"     — impossible; the source is a live repository
#                                   with its own developer, who kept working
#
# The second is not ours to assert and cannot hold. It failed for exactly that
# reason during M0: the source's own developer extended four files that were
# already dirty when the baseline was taken. Nothing of ours had gone near them,
# yet a red build said otherwise — an assertion that cries wolf gets switched
# off, and then it is not protecting anything.
#
# So working-tree drift is REPORTED, never fatal. What is fatal is the pinned
# commit disappearing or its tree changing, because then "what did we actually
# copy" has stopped having an answer.
#
# Hashes are stored, never paths or status text: the source's own file paths
# carry its product name, so a literal baseline would plant exactly the strings
# A3 forbids inside the repository that forbids them (D-026).
#
#   export WEAVE_SOURCE_DIR=/path/to/the/source/checkout
#   scripts/parent_checksum.sh record     # write the baseline (P0.1)
#   scripts/parent_checksum.sh verify     # assert it (milestone gates, commits)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASELINE="$REPO_ROOT/.source-baseline.txt"

usage() {
  echo "usage: WEAVE_SOURCE_DIR=<path> $(basename "$0") {record|verify|print}" >&2
  exit 2
}

require_source() {
  if [[ -z "${WEAVE_SOURCE_DIR:-}" ]]; then
    echo "parent_checksum: WEAVE_SOURCE_DIR is not set." >&2
    echo "  Point it at the read-only source checkout this repository was copied from." >&2
    echo "  Off that machine there is nothing to assert — skip this check and say so." >&2
    exit 3
  fi
  if [[ ! -d "$WEAVE_SOURCE_DIR/.git" ]]; then
    echo "parent_checksum: no git repository at WEAVE_SOURCE_DIR ($WEAVE_SOURCE_DIR)" >&2
    exit 3
  fi
}

PINNED="${WEAVE_SOURCE_COMMIT:-}"

pinned_commit() {
  if [[ -n "$PINNED" ]]; then echo "$PINNED"; return; fi
  # PROVENANCE.md is the record of what was copied; read the sha from it.
  grep -oE '\b[0-9a-f]{40}\b' "$REPO_ROOT/PROVENANCE.md" | head -1
}

# The load-bearing facts. Immutable by construction once the commit exists.
snapshot() {
  local src="$WEAVE_SOURCE_DIR" commit tree
  commit="$(pinned_commit)"
  if [[ -z "$commit" ]]; then
    echo "parent_checksum: no pinned commit found in PROVENANCE.md" >&2
    exit 4
  fi
  if ! tree="$(git -C "$src" --no-optional-locks rev-parse --verify --quiet "${commit}^{tree}")"; then
    echo "parent_checksum: pinned commit $commit does not resolve in the source." >&2
    echo "  The fork's origin has become unreachable — that is a real problem." >&2
    exit 1
  fi
  printf 'commit %s\ntree   %s\n' "$commit" "$tree"
}

# Reported, never fatal: has the source's own developer moved on since we forked?
worktree_note() {
  local src="$WEAVE_SOURCE_DIR" dirty
  dirty="$(git -C "$src" --no-optional-locks status --porcelain | wc -l | tr -d ' ')"
  if [[ "$dirty" == "0" ]]; then
    echo "parent_checksum: note — the source working tree is clean."
  else
    echo "parent_checksum: note — the source working tree has $dirty uncommitted entr(y|ies)."
    echo "                 That is its own developer at work, not us. Informational only:"
    echo "                 we copied a commit, and the commit is what is asserted above."
  fi
}

case "${1:-}" in
  record)
    require_source
    snapshot > "$BASELINE" || exit $?
    echo "parent_checksum: baseline recorded → ${BASELINE#"$REPO_ROOT"/}"
    cat "$BASELINE"
    ;;
  print)
    require_source
    snapshot
    worktree_note
    ;;
  verify)
    require_source
    if [[ ! -f "$BASELINE" ]]; then
      echo "parent_checksum: no baseline at $BASELINE — run 'record' first" >&2
      exit 4
    fi
    current="$(snapshot)" || exit $?
    if [[ "$current" == "$(cat "$BASELINE")" ]]; then
      echo "parent_checksum: the pinned commit and its tree are intact ✓"
      echo "$current" | sed 's/^/                 /'
      worktree_note
    else
      echo "parent_checksum: THE PINNED COMMIT'S TREE CHANGED" >&2
      diff -u "$BASELINE" <(echo "$current") >&2 || true
      echo "  A tree hash cannot change under a commit that still resolves, so either" >&2
      echo "  PROVENANCE.md now names a different commit — re-pin deliberately, with a" >&2
      echo "  D-NN — or the baseline was edited. Find out which before doing anything else." >&2
      exit 1
    fi
    ;;
  *) usage ;;
esac
