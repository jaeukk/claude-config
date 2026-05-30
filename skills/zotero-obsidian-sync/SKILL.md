---
name: zotero-obsidian-sync
description: Reference data and path conventions for syncing Zotero with Obsidian on this WSL setup. Use when working with Zotero references/citations, reading or writing Obsidian notes, or translating Windows paths to WSL paths for PDFs.
---

# Zotero & Obsidian Sync

Conventions for bridging Zotero and Obsidian (ported from Roo's `master-rules`).

## Endpoints & paths

- **Local Zotero API:** `http://localhost:23119`
- **Obsidian vault path:** `/mnt/c/Users/김재욱/My Drive/_WORKSPACE/20_Notes/`

## Path translation

- Convert Windows paths to WSL for all PDF/file paths: `C:\...` → `/mnt/c/...`
  (replace the drive prefix with `/mnt/<drive-letter>/` and switch `\` to `/`).
