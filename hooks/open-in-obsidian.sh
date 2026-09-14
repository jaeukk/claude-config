#!/usr/bin/env bash
# PostToolUse(Write|Edit) hook: open the written .md note in Obsidian.
# Skips subagent writes (batch skills would otherwise flip notes rapidly),
# non-.md files, and files outside any Obsidian vault (no .obsidian ancestor).
# WSL-only: hands an obsidian:// URI to Windows via powershell.exe.

input=$(cat)
[ -n "$(jq -r '.agent_id // empty' <<<"$input")" ] && exit 0
f=$(jq -r '.tool_input.file_path // empty' <<<"$input")
[[ "$f" == *.md ]] || exit 0

vault=$(dirname "$f")
while [ "$vault" != "/" ] && [ ! -d "$vault/.obsidian" ]; do vault=$(dirname "$vault"); done
[ "$vault" = "/" ] && exit 0

rel=${f#"$vault"/}
uri="obsidian://open?vault=$(jq -rn --arg s "$(basename "$vault")" '$s|@uri')&file=$(jq -rn --arg s "${rel%.md}" '$s|@uri')"
cd /mnt/c && powershell.exe -NoProfile -Command "Start-Process '$uri'" >/dev/null 2>&1
exit 0
