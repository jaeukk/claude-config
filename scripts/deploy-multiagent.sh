#!/usr/bin/env bash
# Deploy the canonical multiagent package (~/.claude/multiagent) to a consumer.
#
#   deploy-multiagent.sh global
#       Refresh the global fallback home (~/.multiagent on this machine):
#       policy/ + skills/orchestration, plus a provenance stamp.
#
#   deploy-multiagent.sh project <dir>
#       Install/refresh a project installation at <dir>/_multiagent:
#       full package, but existing tasks/ and _local/ contents are preserved.
#
# Deployed copies are consumers — edit ~/.claude/multiagent in the config repo
# and re-run this script; never edit a deployed copy in place.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SRC="$CLAUDE_DIR/multiagent"
[ -d "$SRC" ] || { echo "ERROR: canonical package not found at $SRC" >&2; exit 1; }

stamp() {  # stamp <target-dir>
  {
    echo "deployed-from: $SRC"
    echo "source-commit: $(git -C "$CLAUDE_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "deployed-at: $(date +%F)"
    echo "rule: do not edit here — edit the config repo and re-run scripts/deploy-multiagent.sh"
  } > "$1/.deployed-from"
}

case "${1:-}" in
  global)
    DST="${2:-$HOME/.multiagent}"
    mkdir -p "$DST/skills"
    rm -rf "$DST/policy" "$DST/skills/orchestration"
    cp -r "$SRC/policy" "$DST/policy"
    cp -r "$CLAUDE_DIR/skills/orchestration" "$DST/skills/orchestration"
    stamp "$DST"
    echo "global deployment refreshed: $DST"
    ;;
  project)
    [ -n "${2:-}" ] || { echo "usage: deploy-multiagent.sh project <dir>" >&2; exit 1; }
    DST="$2/_multiagent"
    mkdir -p "$DST"
    rsync -a --delete \
      --exclude='tasks/*' --exclude='_local/*' \
      --exclude='.ruff_cache' --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='.deployed-from' \
      "$SRC/" "$DST/"
    mkdir -p "$DST/tasks" "$DST/_local"
    touch "$DST/tasks/.gitkeep" "$DST/_local/.gitkeep"
    stamp "$DST"
    echo "project deployment refreshed: $DST"
    ;;
  *)
    echo "usage: deploy-multiagent.sh global [dst] | project <dir>" >&2
    exit 1
    ;;
esac
