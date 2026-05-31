# Roo Code → Claude Code Cheatsheet

## Behavioral "modes" → skills (auto-selected per task, or `/name` to force)

| Roo concept | Claude Code equivalent | How to trigger |
|---|---|---|
| `andrej-karpathy-skills` (Roo skill) | Skill `carefulcoding` (identical content) | Auto when writing/editing code · or `/carefulcoding` |
| `caveman-skill` (Roo skill) | Skill `caveman-skill` | Auto when terse output wanted · or `/caveman-skill` |
| `organizer` custom mode | Skill `organizer` | Auto for file-organizing tasks · or `/organizer` |
| `master-rules` (Zotero/Obsidian) | Skill `zotero-obsidian-sync` | Auto for Zotero/Obsidian work · or `/zotero-obsidian-sync` |
| `searcher` (💽 Find) custom mode | Subagent `searcher` | "use the searcher subagent" (delegated, read-only) |
| `note` (✍️ Obsidian Note Specialist) custom mode | Subagent `note` | "use the note subagent" (delegated) |
| `code` mode rules (physics standards) | Always-on in `~/.claude/CLAUDE.md` | Loaded every session |
| `rules/00-persona.md` (identity, language) | `~/.claude/CLAUDE.md` | Loaded every session |

**Key difference:** skills are evaluated *per task*, not held as sticky state. There is no
"I'm now in X mode until I switch." Claude picks the matching skill each turn from its description.

## Operational modes (NOT personas) — cycle with Shift+Tab
- **normal** — asks permission before edits
- **auto-accept edits** — applies edits without prompting
- **plan mode** — researches & plans, makes no changes until you approve

These control *permissions*, not behavior. Don't confuse with Roo's code/debug/ask modes.

## Where things live
- Global rules:  `~/.claude/CLAUDE.md`
- Skills:        `~/.claude/skills/<name>/SKILL.md`
- Subagents:     `~/.claude/agents/<name>.md`
- Slash commands:`~/.claude/commands/<name>.md`
- Hooks scripts: `~/.claude/hooks/<name>.sh` (wired up in `settings.json`)

## Want persistent roles like Roo modes?
Create a subagent in `~/.claude/agents/`. It's *delegated* ("use the X subagent"), runs in its
own isolated context — the closest thing to flipping into a mode and staying there.

## Keeping this config in sync (`~/.claude` *is* the git repo)
This whole `~/.claude` directory is a checkout of `github.com/jaeukk/claude-config`.
- **Pull on session start (notify-only):** a `SessionStart` hook runs
  `hooks/check-config-updates.sh`, which fetches and warns if you're behind `origin/main`.
  It never auto-pulls — update with `git -C ~/.claude pull --ff-only`.
- **Push your changes:** run **`/push-config`** to commit the tracked config and push to GitHub.
