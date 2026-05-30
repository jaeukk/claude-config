# Roo Code → Claude Code Cheatsheet

## Behavioral "modes" → skills (auto-selected per task, or `/name` to force)

| Roo concept | Claude Code equivalent | How to trigger |
|---|---|---|
| `carefulcoding` skill | Skill `carefulcoding` | Auto when writing/editing code · or `/carefulcoding` |
| `organizer` custom mode | Skill `organizer` | Auto for file-organizing tasks · or `/organizer` |
| `master-rules` (Zotero/Obsidian) | Skill `zotero-obsidian-sync` | Auto for Zotero/Obsidian work · or `/zotero-obsidian-sync` |
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
- Subagents:     `~/.claude/agents/<name>.md`  (optional)

## Want persistent roles like Roo modes?
Create a subagent in `~/.claude/agents/`. It's *delegated* ("use the X subagent"), runs in its
own isolated context — the closest thing to flipping into a mode and staying there.
