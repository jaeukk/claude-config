---
name: zotero-obsidian-sync
description: Conventions and per-machine path resolution for syncing Zotero with Obsidian on a WSL setup. Use when working with Zotero references/citations, reading or writing Obsidian notes, or translating Windows paths to WSL paths for PDFs. The Obsidian vault path is resolved at runtime (never hardcoded), so this skill keeps working after syncing to a machine with a different Windows user folder.
---

# Zotero & Obsidian Sync

Conventions for bridging Zotero and Obsidian (ported from Roo's `master-rules`), written to
survive moving between machines.

## Zotero
- **Local Zotero API:** `http://localhost:23119` (identical on every machine).
- The user's Zotero **userID is `5872032`** (used in `/api/users/5872032/...` paths).

### Reading
- `GET http://localhost:23119/api/users/5872032/items`, `/collections`, `/collections/<KEY>/items/top`, `.../items/<KEY>` — list/search/fetch. Also exposed via the `mcp__zotero__*` tools (search / metadata / fulltext) and Better BibTeX JSON-RPC at `/better-bibtex/json-rpc`.

### Writing — the local REST API is READ-ONLY; use the Connector
`POST` to the local REST API fails with `400 "Endpoint does not support method"`. **Do not give up there** — write through the **Zotero Connector** (the channel the browser "Save to Zotero" button uses), which saves into the **currently-selected collection**:

1. `POST http://localhost:23119/connector/getSelectedCollection` (body `{}`) → confirms the target, e.g. `{"name":"MC_method","editable":true,...}`. Ask the user to click the intended collection in Zotero if it's not selected (the Connector has no "choose collection" parameter).
2. `POST http://localhost:23119/connector/saveItems` with headers `Content-Type: application/json` and `X-Zotero-Connector-API-Version: 3`, body:
   ```json
   {"sessionID":"<uuid>","uri":"https://...","items":[ <translator-format items> ]}
   ```
   Returns HTTP **201**. Item format = Zotero translator JSON: `itemType`, `creators:[{creatorType,firstName,lastName}]`, `title`, `date`, `publicationTitle`/`proceedingsTitle`, `volume`, `issue`, `pages`, `DOI`, `url`, `tags:[{tag}]`. **No `collections` field** — it follows the selected collection.

Best practice: get authoritative fields from **CrossRef** (`https://api.crossref.org/works/<DOI>`) before building items, batch all items in one `saveItems` array, then **verify** with the read API (`GET /api/users/5872032/collections/<KEY>/items/top`). Creating a *collection* is NOT supported over the API — ask the user to make it in the Zotero UI.

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
