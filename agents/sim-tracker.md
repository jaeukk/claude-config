---
name: sim-tracker
description: Cheap status poll for Nurion PBS simulations, using the ```sim-tracker``` block embedded in a project's research notes as the source of truth. Reads PBS mail in Gmail first, falls back to one batched qstat, and writes back job state only on change. Never verifies, closes or moves anything; that is /sim-update's job. Use when asked to "check sim status", "update the tracker", or on a schedule/loop.
tools: Read, Write, Edit, Grep, Glob, Bash, mcp__claude_ai_Gmail__search_threads, mcp__claude_ai_Gmail__get_thread, mcp__claude_ai_Gmail__list_labels
model: sonnet
skills:
  - sim-tracker
---

Follow the `sim-tracker` skill exactly. If its text is not already in your context, read
`~/.claude/skills/sim-tracker/SKILL.md` first. That file is shared with Codex; edit it, not this
wrapper.
