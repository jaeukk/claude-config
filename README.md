# claude-config

Portable [Claude Code](https://claude.com/claude-code) configuration for **Jaeuk Kim** —
synced across machines via this repo. The checkout *is* `~/.claude`: the home Claude directory is a
working tree of this repository, with secrets and machine-local runtime state gitignored.

## What's tracked

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Always-on global instructions (identity, language, physics coding standards). |
| `settings.json` | Global settings + the `SessionStart` update-check hook. |
| `skills/` | Skills auto-selected per task, or forced with `/<name>`. |
| `agents/` | Subagents — delegated, persistent-role helpers ("use the X subagent"). |
| `commands/` | Custom slash commands. |
| `hooks/` | Scripts wired into lifecycle events via `settings.json`. |
| `roo-to-claude.md` | Cheatsheet mapping the old Roo Code setup to its Claude Code equivalents. |

Everything else under `~/.claude` (credentials, `sessions/`, `projects/`, caches, and
`settings.local.json` for machine-local overrides) is intentionally **not** tracked — see
[.gitignore](.gitignore).

## Skills

- **`carefulcoding`** — think-before-coding guidelines (simplicity, surgical changes, goal-driven).
- **`caveman-skill`** — extreme brevity / code-first output.
- **`organizer`** — read and reorganize files in the filesystem.
- **`zotero-obsidian-sync`** — Zotero ↔ Obsidian conventions with per-machine vault-path resolution.

## Subagents

- **`searcher`** — read-only research: search Zotero, read Obsidian notes, retrieve metadata.
- **`note`** — Obsidian note specialist (Zettelkasten, LaTeX, template-driven).

Both resolve Zotero/Obsidian paths via the `zotero-obsidian-sync` skill rather than hardcoding a
Windows user folder, so they stay portable across machines.

## Staying in sync

- **On session start (notify-only):** `hooks/check-config-updates.sh` fetches and warns if the
  local checkout is behind `origin/main`. It never auto-pulls — update manually:
  ```bash
  git -C ~/.claude pull --ff-only
  ```
- **Pushing changes:** run **`/push-config`** in any Claude Code session to commit the tracked
  config and push to GitHub.

## Notification sound

A `Notification` hook runs `hooks/notify-sound.sh`, which plays an alarm tone whenever Claude Code
needs your answer or permission. It uses `powershell.exe` beeps on WSL, `afplay` on macOS, and
`paplay`/`aplay` on Linux, falling back to the terminal bell — so it's a no-op (never errors) on a
machine with no audio path.

## Setting up on a new machine

```bash
# Back up an existing ~/.claude if present, then make it this repo's checkout:
cd ~/.claude
git init
git remote add origin git@github.com:jaeukk/claude-config.git   # SSH; or use HTTPS + a credential helper
git fetch origin
git checkout -b main --track origin/main
```

The `.gitignore` ignores everything by default and re-includes only the portable config, so this
won't touch credentials or runtime state already in `~/.claude`. The update hook and `/push-config`
assume the remote can authenticate non-interactively (SSH key or a configured credential helper).
