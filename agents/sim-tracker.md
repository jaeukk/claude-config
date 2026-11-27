---
name: sim-tracker
description: Cheap status poll for sim-tracker blocks (PBS on Nurion, Slurm on Neuron or Della, local desktop/laptop runs) in a project's research notes. Runs one script that queries the schedulers, saves evidence, and writes back changed job states; the agent only relays its report. Never verifies, closes or moves anything; that is /sim-update's job. Use when asked to "check sim status", "update the tracker", or on a schedule/loop.
tools: Bash, Read
model: haiku
skills:
  - sim-tracker
---

Follow the `sim-tracker` skill exactly. If its text is not already in your context, read
`~/.claude/skills/sim-tracker/SKILL.md` first. That file is shared with Codex; edit it, not this
wrapper.
