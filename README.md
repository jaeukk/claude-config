# claude-config

Portable [Claude Code](https://claude.com/claude-code) configuration for **Jaeuk Kim** —
synced across machines via this repo. The checkout *is* `~/.claude`: the home Claude directory is a
working tree of this repository, with secrets and machine-local runtime state gitignored.

## What's tracked

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Always-on global instructions (identity, language, physics coding standards). |
| `settings.json` | Global settings + the `SessionStart` update-check hook. |
| `settings.team.json` | Team-account overlay (`model: opus`); `compose-settings.py` writes `~/.claude-team/settings.json` = composed settings + this layer, as a real file. |
| `skills/` | Skills auto-selected per task, or forced with `/<name>`. |
| `agents/` | Subagents — delegated, persistent-role helpers ("use the X subagent"). |
| `commands/` | Custom slash commands. |
| `hooks/` | Scripts wired into lifecycle events via `settings.json`. |
| `shell/bash_aliases.sh` | Portable interactive Bash functions sourced through `~/.bash_aliases`. |
| `shell/rsync-exclude` | Shared rsync exclude patterns, linked as `~/.rsync-exclude`. |
| `roo-to-claude.md` | Cheatsheet mapping the old Roo Code setup to its Claude Code equivalents. |

Everything else under `~/.claude` (credentials, `sessions/`, `projects/`, caches, and
`settings.local.json` for machine-local overrides) is intentionally **not** tracked — see
[.gitignore](.gitignore).

## Skills

- **`karpathy-guidelines`** — think-before-coding guidelines (simplicity, surgical changes,
  goal-driven), from [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
  (replaces the earlier `carefulcoding` copy of the same content).
- **`caveman-skill`** — extreme brevity / code-first output.
- **`organizer`** — read and reorganize files in the filesystem.
- **`zotero-obsidian-sync`** — Zotero ↔ Obsidian conventions with per-machine vault-path resolution.

### Which venue sees which skill

`skills/` is the one source copy for Claude Code, Codex and the Antigravity CLI (`agy`). Each
venue sees only the skills that work there; `scripts/link-skills.sh` holds the two lists and
applies them (symlinks in `~/.codex/skills`, an `include_only` allowlist in
`~/.gemini/config/skills.json`). Run it after adding, renaming or removing a skill. Never copy a
skill into another venue's directory: a copy drifts.

| Skill | Claude Code | Codex | agy |
|---|---|---|---|
| `multiagent` | yes | yes | no (agy is not a conductor host) |
| `karpathy-guidelines` | yes | no | yes |
| `math-unicode` | yes | yes, through `~/.agents/skills` | yes |
| every other skill here | yes | yes | yes |
| Orca's `computer-use`, `orca-cli`, `orchestration` (installed in `~/.agents/skills`; this repo tracks only the links) | yes, linked | yes, read there directly | yes |

A skill body is venue-neutral; where tools differ it carries a short venue note (`md-report`
publishing, `research-note` checking, `sim-update` Gmail fallback, Zotero MCP tools).

## Subagents

- **`searcher`** — read-only research: search Zotero, read Obsidian notes, retrieve metadata.
- **`note`** — Obsidian note specialist (Zettelkasten, LaTeX, template-driven).
- **`paper-reviewer`** — summarizes a single paper from the local Zotero library into a structured
  Markdown review (algorithm / methods / results).
- **`book-summarizer`** — digests a chaptered book or textbook from Zotero (or a PDF) into
  per-chapter Obsidian notes with LaTeX, figures, and wikilinks.

All of them resolve Zotero/Obsidian paths via the `zotero-obsidian-sync` skill rather than
hardcoding a Windows user folder, so they stay portable across machines.

## Staying in sync

- **On session start (notify-only):** `hooks/check-config-updates.sh` fetches and warns if the
  local checkout is behind `origin/main`. It never auto-pulls.
- **Updating this machine:** run `update-config`. It refuses a dirty worktree, validates the
  remote Bash fragment before fast-forwarding, composes live settings, and checks the managed
  `~/.bash_aliases` symlink. Machine-specific shell values live outside Git in
  `~/.bash_aliases.local`; start from `shell/bash_aliases.local.example` on a new machine.
  The underlying command remains available by path during bootstrap:
  ```bash
  ~/.claude/scripts/update-config.sh
  ```
- **Pushing changes:** run **`/push-config`** in any Claude Code session to commit the tracked
  config and push to GitHub.

## Notification sound & status line

- **`hooks/notify-sound.sh`** (`Notification` hook) plays an alarm tone whenever Claude Code needs
  your answer or permission. It uses `powershell.exe` beeps on WSL, `afplay` on macOS, and
  `paplay`/`aplay` on Linux, falling back to the terminal bell — so it's a no-op (never errors) on
  a machine with no audio path.
- **`hooks/notify-on-question.sh`** (`Stop` hook) plays the same sound when Claude's *final*
  message asks for a decision or input, and stays silent on purely informational turns — so the
  alarm always means "your input is needed".
- **`hooks/statusline.py`** renders the status line: model, live context-token usage (parsed from
  the session transcript), and session cost.

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
