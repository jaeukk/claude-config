#!/usr/bin/env bash
# Check that every reading agent still defers to the extraction contract.
#
# These definitions cannot be kept in sync by diffing them against each other:
# their host layers are *supposed* to differ (WSL vs native paths, Zotero
# annotations, TOML vs Markdown). What must stay identical is that none of them
# restates a reading rule. That is what this checks.
#
#   bash _shared/adapters/lint_agent_contracts.sh
#
# Exit 0 = clean, 1 = drift found.

set -uo pipefail
VAULT=/home/jaeukk/20_Notes
CONTRACT=$VAULT/_shared/contracts/document-note.md
ADAPTERS=(
  /home/jaeukk/.claude/agents/book-summarizer.md
  /home/jaeukk/.claude/agents/paper-reviewer.md
  "$VAULT/.claude/agents/book-summarizer.md"
  "$VAULT/.claude/agents/paper-reviewer.md"
  "$VAULT/.codex/agents/book-summarizer.toml"
  "$VAULT/.codex/agents/paper-reviewer.toml"
)
fail=0

echo "== contract =="
if [ -f "$CONTRACT" ] && grep -q 'END OF CONTRACT' "$CONTRACT"; then
  echo "  ok  $CONTRACT ($(md5sum "$CONTRACT" | cut -c1-8))"
else
  echo "  FAIL  missing contract or END marker: $CONTRACT"; fail=1
fi

echo "== adapters =="
for f in "${ADAPTERS[@]}"; do
  [ -f "$f" ] || { echo "  FAIL  missing: $f"; fail=1; continue; }
  msg=""
  grep -q 'contracts/document-note.md' "$f" || msg+=" no-contract-ref"
  grep -q 'Do not proceed from memory' "$f" || msg+=" no-fail-closed"
  # reading rules that must live only in the contract
  grep -qi 'not negotiable\|every displayed equation\|key equations' "$f" && msg+=" RESTATES-EQUATION-RULE"
  grep -qi 'never reproduce running heads' "$f" && msg+=" RESTATES-FURNITURE-RULE"
  grep -q '\[!define\]' "$f" && msg+=" RESTATES-CALLOUT-VOCAB"
  # paths that no longer exist
  grep -q '40_Resources/20_Books' "$f" && msg+=" STALE-BOOK-ROOT"
  if [ -n "$msg" ]; then echo "  FAIL $f:$msg"; fail=1; else echo "  ok   $f"; fi
done

echo "== TOML integrity (a stray ''' breaks these silently) =="
python3 - <<'PY' || fail=1
import tomllib, pathlib, sys
ok = True
for f in ("book-summarizer.toml", "paper-reviewer.toml"):
    p = pathlib.Path("/home/jaeukk/20_Notes/.codex/agents") / f
    try:
        tomllib.loads(p.read_text(encoding="utf-8")); print(f"  ok   {p.name}")
    except Exception as exc:
        print(f"  FAIL {p.name}: {exc}"); ok = False
sys.exit(0 if ok else 1)
PY

echo "== orphaned Claude Code worktrees =="
if [ -d "$VAULT/.claude/worktrees" ] && [ ! -d "$VAULT/.git/worktrees" ]; then
  echo "  FAIL  $(ls -1 "$VAULT/.claude/worktrees" | wc -l) orphaned worktree dir(s) carrying stale agent copies"
  fail=1
else
  echo "  ok   none"
fi

[ "$fail" = 0 ] && echo "CLEAN" || echo "DRIFT FOUND"
exit $fail
