#!/usr/bin/env bash
# Expose the skills in ~/.claude/skills to the other agent venues on this machine.
#
# One source copy, per-venue lists: each venue sees only the skills that work there.
#   Claude Code  reads ~/.claude/skills directly (everything).
#   Codex        reads ~/.codex/skills and ~/.agents/skills: one symlink per listed skill.
#   agy          reads ~/.gemini/config/skills.json: an include_only allowlist on ~/.claude/skills.
#
# Idempotent. It creates or repoints the symlinks named in the lists, removes a Codex link
# only when it points into ~/.claude/skills at a skill that is gone, and in skills.json touches
# only the entry for ~/.claude/skills. Anything else in the way (a real directory, a file, a
# dangling link to somewhere else) is reported or left alone. Run it after adding, renaming or
# removing a skill, and edit the two lists below to change what a venue sees.
set -euo pipefail

SRC="$HOME/.claude/skills"

# Codex: no karpathy-guidelines (generic coding behavior Codex needs no skill for);
# math-unicode reaches Codex through ~/.agents/skills below.
CODEX=(caveman-skill cpp-guidelines finding-unknowns md-report multiagent organizer
       prepare-arxiv-submission python-guidelines research-note sim-tracker sim-update
       zotero-obsidian-sync)

# agy: no multiagent (agy is not a conductor host and has no backend in the engine).
AGY=(caveman-skill computer-use cpp-guidelines finding-unknowns karpathy-guidelines math-unicode
     md-report orca-cli orchestration organizer prepare-arxiv-submission python-guidelines
     research-note sim-tracker sim-update zotero-obsidian-sync)

status=0

# link <target> <link path>: create or repoint a symlink, never replace a real file or directory.
link() {
  if [ ! -d "$1" ]; then
    echo "MISSING $1: not linked" >&2; status=1
  elif [ -L "$2" ]; then
    ln -sfn "$1" "$2"
  elif [ -e "$2" ]; then
    echo "SKIP $2: exists and is not a symlink; move it away and rerun" >&2; status=1
  else
    ln -s "$1" "$2"; echo "linked $2"
  fi
}

if [ -d "$HOME/.codex" ]; then
  mkdir -p "$HOME/.codex/skills"
  for s in "${CODEX[@]}"; do link "$SRC/$s" "$HOME/.codex/skills/$s"; done
  for l in "$HOME/.codex/skills"/*; do  # one of our links whose skill is gone (renamed or removed)
    if [ -L "$l" ] && [ ! -e "$l" ] && [[ "$(readlink "$l")" == "$SRC"/* ]]; then
      rm "$l"; echo "removed dangling link $l"
    fi
  done
fi

if [ -d "$HOME/.agents/skills" ]; then
  link "$SRC/math-unicode" "$HOME/.agents/skills/math-unicode"
fi

# agy: set include_only on the entry for ~/.claude/skills (adding the entry if absent). Every
# other entry and key of an existing skills.json is kept. Written through a temporary file.
if [ -d "$HOME/.gemini/config" ]; then
  python3 -c '
import json, os, sys
path, src, names = sys.argv[1], sys.argv[2], sys.argv[3:]
assert src and names, "empty source path or skill list"
doc = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
entries = doc.setdefault("entries", [])
mine = [e for e in entries if os.path.expanduser(e.get("path", "")).rstrip("/") == src]
if not mine:
    mine = [{"path": src}]
    entries.append(mine[0])
for e in mine:
    e["include_only"] = names
    e.pop("exclude", None)
with open(path + ".tmp", "w") as f:
    f.write(json.dumps(doc, indent=2) + chr(10))
os.replace(path + ".tmp", path)
' "$HOME/.gemini/config/skills.json" "$SRC" "${AGY[@]}"
fi

exit "$status"
