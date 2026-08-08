#!/usr/bin/env bash
# parent_checksum.sh — prove we never wrote to the read-only source tree (A2, D-003).
#
# Weave is a one-way copy. The source repository is a source, never a dependency
# and never a write target, and an intention is not a control — so this records
# what the source looked like before P0 and asserts it afterwards.
#
# Two facts are captured:
#   1. `git status --porcelain` — any file added, removed or modified.
#   2. a sha256 over the content of every tracked file — an in-place edit that a
#      dirty-tree glance would show but nobody would diff.
#
# BOTH ARE STORED AS HASHES, NOT AS TEXT, and that is deliberate. The source's
# own file paths carry its product name, so committing the raw status output
# would plant exactly the strings A3 forbids inside the repository that forbids
# them — the name-guard would fail on its own evidence file.
#
# The source location is NOT hard-coded here for the same reason. Set it:
#
#     export WEAVE_SOURCE_DIR=/path/to/the/source/checkout
#     scripts/parent_checksum.sh record     # write the baseline (P0.1)
#     scripts/parent_checksum.sh verify     # assert it (M0 gate, and every commit)
#
# Read-only by construction: --no-optional-locks keeps git from refreshing the
# index, and nothing here writes anywhere inside the source tree.

set -euo pipefail

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

# Emit the baseline: pinned head, and one hash per fact. No paths, ever.
snapshot() {
  local src="$WEAVE_SOURCE_DIR"
  local head status_hash content_hash
  head="$(git -C "$src" --no-optional-locks rev-parse HEAD)"
  status_hash="$(
    git -C "$src" --no-optional-locks status --porcelain \
      | LC_ALL=C sort | sha256sum | awk '{print $1}'
  )"
  content_hash="$(
    git -C "$src" --no-optional-locks ls-files -z \
      | LC_ALL=C sort -z \
      | ( cd "$src" && xargs -0 sha256sum 2>/dev/null ) \
      | sha256sum | awk '{print $1}'
  )"
  printf 'head    %s\nstatus  %s\ncontent %s\n' "$head" "$status_hash" "$content_hash"
}

case "${1:-}" in
  record)
    require_source
    snapshot > "$BASELINE"
    echo "parent_checksum: baseline recorded → ${BASELINE#"$REPO_ROOT"/}"
    ;;
  print)
    require_source
    snapshot
    ;;
  verify)
    require_source
    if [[ ! -f "$BASELINE" ]]; then
      echo "parent_checksum: no baseline at $BASELINE — run 'record' first" >&2
      exit 4
    fi
    if diff -u "$BASELINE" <(snapshot) >/dev/null; then
      echo "parent_checksum: source tree unchanged since the baseline ✓"
    else
      echo "parent_checksum: THE SOURCE TREE CHANGED — A2/D-003 violated" >&2
      diff -u "$BASELINE" <(snapshot) >&2 || true
      echo "  'head' differs      → the source moved on; that is fine, re-pin deliberately." >&2
      echo "  'status'/'content'  → something wrote into it. Find it before doing anything else." >&2
      exit 1
    fi
    ;;
  *) usage ;;
esac
