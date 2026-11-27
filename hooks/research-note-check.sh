#!/usr/bin/env bash
# PostToolUse(Write|Edit) hook: after a daily research note is written, run the
# research-note skill's mechanical checker and hand its findings back to the
# model as additionalContext. Silent when the file is not a daily note or the
# checker finds nothing. Never blocks (the edit already happened).
input=$(cat)
f=$(jq -r '.tool_input.file_path // empty' <<<"$input")
[[ "$f" =~ /10_ResearchNotes/0[12]_(NotConfirmed|Confirmed)/[0-9]{4}-[0-9]{2}-[0-9]{2}\.md$ ]] || exit 0
[ -f "$f" ] || exit 0
out=$(python3 "$HOME/.claude/skills/research-note/scripts/check_note.py" "$f" 2>&1)
[ -z "$out" ] && exit 0
jq -cn --arg ctx "research-note check of $f:"$'\n'"$out"$'\n'"Fix ERRORs on lines you wrote; judge WARNs; report findings on lines you did not touch." \
  '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$ctx}}'
