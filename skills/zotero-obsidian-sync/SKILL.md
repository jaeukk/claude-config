---
name: zotero-obsidian-sync
description: Zotero read and write recipes and per-machine resolution of the Obsidian vault path on this WSL setup. Use when working with Zotero references, citations, tags or collections, when a task needs the vault's location, or when translating Windows paths (PDFs returned by Zotero) to WSL paths. The vault path is resolved at runtime, never hardcoded, so this keeps working on a machine with a different Windows user folder.
---

# Zotero and Obsidian

## Zotero: two services

| | Local REST API `http://localhost:23119/api` | Zotero Web API `https://api.zotero.org` |
|---|---|---|
| what it is | the desktop app's loopback server | Zotero's cloud sync service |
| reads | yes | yes |
| writes | no (`PATCH`/`PUT` → 501, `POST` → 400) | yes, with the write-enabled API key |

Both use the same userID, `5872032`, and the same item keys, so the URLs look alike. Check the
host before concluding anything: a write failure on `localhost:23119` says nothing about
`api.zotero.org`.

### Read

`GET http://localhost:23119/api/users/5872032/items`, `/collections`,
`/collections/<KEY>/items/top`, `/items/<KEY>`. Better BibTeX JSON-RPC is at
`/better-bibtex/json-rpc`. Where a Zotero MCP server is configured (Claude Code:
`mcp__zotero__*` for search, metadata and full text), it wraps the same local API; on a venue
without it, call the HTTP endpoints.

### Add new items: the Connector

The local REST API cannot write, but the Zotero Connector (the channel behind the browser's
"Save to Zotero" button) can. It saves into the collection currently selected in the Zotero app.

1. `POST http://localhost:23119/connector/getSelectedCollection` with body `{}` confirms the
   target, e.g. `{"name":"MC_method","editable":true,...}`. The Connector has no parameter for
   choosing a collection: if the wrong one is selected, ask the user to click the right one.
2. `POST http://localhost:23119/connector/saveItems` with headers `Content-Type: application/json`
   and `X-Zotero-Connector-API-Version: 3`, and body

   ```json
   {"sessionID":"<uuid>","uri":"https://...","items":[ <translator-format items> ]}
   ```

   It returns HTTP 201. An item is Zotero translator JSON: `itemType`,
   `creators:[{creatorType,firstName,lastName}]`, `title`, `date`,
   `publicationTitle`/`proceedingsTitle`, `volume`, `issue`, `pages`, `DOI`, `url`,
   `tags:[{tag}]`. It takes no `collections` field.

Take the fields from CrossRef (`https://api.crossref.org/works/<DOI>`), send all items in one
`saveItems` call, then verify with `GET /api/users/5872032/collections/<KEY>/items/top`. A
collection cannot be created over either API: ask the user to make it in the Zotero app.

### Change existing items: the Web API, through the vault scripts

A new item takes its tags in `saveItems` (`{"tag":"w:raw-build"}` for the `[[Wiki_Schema]]`
raw-build convention). An item already in the library is changed through the Web API, with the
scripts at the vault root:

- `python3 99_SYSTEM/scripts/zotero_add_tag.py w:raw-build <KEY> [<KEY> ...]` adds a tag. It is
  idempotent ("already tagged") and prints `PATCH 204` on success.
- `python3 99_SYSTEM/scripts/zotero_add_collection.py <COLLECTION_KEY> <KEY> ...` files existing
  items into a collection (the Connector cannot file an item that already exists). Idempotent.

Use these commands in exactly this form. The owner approved Web API writes for them
(2026-09-30); on Claude Code the vault's `.claude/settings.local.json` allows exactly these
commands, and auto mode blocks the same write done another way (inline `python3 -`,
`curl -X PATCH`).

What the scripts do, for when one has to be extended: `GET
https://api.zotero.org/users/5872032/items/<KEY>` for the item's `version` and current field,
then `PATCH` the same URL with headers `Zotero-API-Key: <key>` and
`If-Unmodified-Since-Version: <version>`, returning HTTP 204. `PATCH` replaces a field, so send the
whole `tags` (or `collections`) array and keep `type:1` on automatic tags. The write lands in the
cloud library and reaches the desktop app only while its Sync is on.

**API key.** The write-enabled key is in `~/.config/zotero/api_key` (mode 600; strip the trailing
newline). Read it inside the script that makes the call. Never copy it into `~/.claude.json`,
a settings file, a shell export, a note, or command output.

Dead ends on the local app, not worth retrying: `PATCH`/`PUT`/`POST` to
`localhost:23119/api/users/5872032/items/<KEY>` (501, 501, 400); Better BibTeX JSON-RPC has no
`item.tag.*` method; re-saving an existing item through `saveItems` to merge tags is untested and
risks a duplicate.

## Resolve the Obsidian vault path

The vault is in Google Drive under the stable suffix `My Drive/_WORKSPACE/20_Notes`, but the
Windows user-profile segment (`/mnt/c/Users/<WINDOWS_USER>/...`) differs per machine, so never
hardcode it. Resolve it with the one implementation every skill here shares:

```bash
VAULT=$(python3 ~/.claude/skills/research-note/scripts/find_root.py --vault)
```

It takes the first of: `$OBSIDIAN_VAULT`; the path pinned in `~/.claude/zotero-obsidian.local`
(gitignored, one per machine, read by every venue); a glob of the stable suffix under
`/mnt/<drive>/Users/*/` or `/mnt/<drive>/`. A candidate counts only if it holds
`20_Notes_Policy.md`. It exits 1 with nothing on stdout when no vault is found, or when several
distinct vaults match (it lists them on stderr). In either case ask the user which path to use
and offer to pin the answer in `~/.claude/zotero-obsidian.local`; never pick one yourself.

## Translate Windows paths to WSL

Zotero returns Windows paths for PDFs and attachments. Convert them with `wslpath -u
'C:\Users\Foo\paper.pdf'` (gives `/mnt/c/Users/Foo/paper.pdf`).
