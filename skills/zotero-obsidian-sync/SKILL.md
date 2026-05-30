---
name: zotero-obsidian-sync
description: Conventions and per-machine path resolution for syncing Zotero with Obsidian on a WSL setup. Use when working with Zotero references/citations, reading or writing Obsidian notes, or translating Windows paths to WSL paths for PDFs. The Obsidian vault path is resolved at runtime (never hardcoded), so this skill keeps working after syncing to a machine with a different Windows user folder.
---

# Zotero & Obsidian Sync

Conventions for bridging Zotero and Obsidian (ported from Roo's `master-rules`), written to
survive moving between machines.

## Zotero
- **Local Zotero API:** `http://localhost:23119` (identical on every machine).

## Resolving the Obsidian vault path — DO NOT hardcode it

The vault lives in Google Drive under the stable suffix **`My Drive/_WORKSPACE/20_Notes`**, but the
Windows user-profile segment (`/mnt/c/Users/<WINDOWS_USER>/...`) differs per machine. Resolve it in
this order and use the first hit:

1. **Machine-local override (preferred).** Use `$OBSIDIAN_VAULT` if set, else the path stored in
   `~/.claude/zotero-obsidian.local` if that file exists. This file is intentionally NOT synced
   (gitignored), so each machine pins its own value once.
   ```bash
   VAULT="${OBSIDIAN_VAULT:-$(cat ~/.claude/zotero-obsidian.local 2>/dev/null)}"
   ```

2. **Auto-detect** by globbing the stable marker (works without any setup):
   ```bash
   [ -z "$VAULT" ] && VAULT=$(ls -d /mnt/[a-z]/Users/*/"My Drive"/_WORKSPACE/20_Notes 2>/dev/null | head -1)
   # Google Drive mounted as its own drive letter instead of under the user profile:
   [ -z "$VAULT" ] && VAULT=$(ls -d /mnt/[a-z]/"My Drive"/_WORKSPACE/20_Notes 2>/dev/null | head -1)
   ```
   If the glob returns more than one match, ask the user which profile to use.

3. **Derive from the Windows username** as a last resort:
   ```bash
   [ -z "$VAULT" ] && WUSER=$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r\n') \
     && VAULT="/mnt/c/Users/$WUSER/My Drive/_WORKSPACE/20_Notes"
   ```

If nothing resolves, ask the user for the vault path and offer to save it to
`~/.claude/zotero-obsidian.local` so it's pinned for next time.

## Path translation (Windows → WSL)

Prefer `wslpath` (handles drive letter + slashes correctly):
```bash
wslpath -u 'C:\Users\Foo\paper.pdf'     # -> /mnt/c/Users/Foo/paper.pdf
```
Manual fallback: `<DRIVE>:\path\to\x` → `/mnt/<drive-letter-lowercased>/path/to/x`, switching every
`\` to `/`. Apply to all PDF/file paths returned by the Zotero API.
