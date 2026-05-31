#!/usr/bin/env bash
# SessionStart hook: notify (do NOT pull) when ~/.claude is behind origin/main.
# Always exits 0 so it can never block a session.

cd "$HOME/.claude" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Refresh remote tracking ref; bail quietly if offline.
git fetch --quiet origin main 2>/dev/null || exit 0

LOCAL=$(git rev-parse @ 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)
BASE=$(git merge-base @ origin/main 2>/dev/null)

# Warn only when strictly behind (local is an ancestor of remote and they differ).
if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ] && [ "$LOCAL" = "$BASE" ]; then
  COUNT=$(git rev-list --count @..origin/main 2>/dev/null)
  echo "⚠️  Claude config is ${COUNT} commit(s) behind origin/main. To update: git -C ~/.claude pull --ff-only"
fi

exit 0
