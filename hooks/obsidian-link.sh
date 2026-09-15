#!/usr/bin/env bash
# PostToolUse(Write|Edit) hook: show a Ctrl+Shift+click-able link to a written
# Obsidian note in the Orca terminal.
# Orca opens only http/https/file links (obsidian:// is dead text), and .md opens
# in the Windows default editor, so we write a Windows .url shortcut pointing at
# the obsidian:// URI and print its path; Ctrl+Shift+click opens it with the
# system default, which follows the shortcut into Obsidian.
# Skips subagent writes, non-.md files, files outside any vault (no .obsidian
# ancestor), and non-Orca sessions (link would not be clickable anyway).

input=$(cat)
[ -n "$(jq -r '.agent_id // empty' <<<"$input")" ] && exit 0
[ -n "$ORCA_USER_DATA_PATH" ] || exit 0
f=$(jq -r '.tool_input.file_path // empty' <<<"$input")
[[ "$f" == *.md ]] || exit 0

vault=$(dirname "$f")
while [ "$vault" != "/" ] && [ ! -d "$vault/.obsidian" ]; do vault=$(dirname "$vault"); done
[ "$vault" = "/" ] && exit 0

rel=${f#"$vault"/}
uri="obsidian://open?vault=$(jq -rn --arg s "$(basename "$vault")" '$s|@uri')&file=$(jq -rn --arg s "${rel%.md}" '$s|@uri')"

# Windows %LOCALAPPDATA%\Temp, derived from Orca's .../AppData/Roaming/orca.
# Short path hash keeps same-named notes (e.g. daily notes) from colliding.
# ponytail: shortcuts are never cleaned up; ~100 bytes each in Temp.
dir="${ORCA_USER_DATA_PATH%/AppData/*}/AppData/Local/Temp/obsidian-links"
name=$(basename "${f%.md}" | tr ' ' _)-$(printf %s "$f" | md5sum | cut -c1-6).url
mkdir -p "$dir" && printf '[InternetShortcut]\r\nURL=%s\r\n' "$uri" > "$dir/$name" || exit 0

jq -nc --arg m "Obsidian (Ctrl+Shift+click): $dir/$name" '{systemMessage: $m}'
