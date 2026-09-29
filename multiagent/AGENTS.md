# Multiagent — maintainer notes

This file loads for sessions started in `multiagent/` (`AGENTS.md` and `CLAUDE.md` are identical
copies). It covers maintaining the installation. The conductor procedure is
`../skills/multiagent/SKILL.md`; follow that, not this file, when conducting. Do not copy this
file into a global `CLAUDE.md`.

## Operating Principles

The four principles — Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven
Execution — have one authoritative copy: the `karpathy-guidelines` skill. **Before conducting,
read the complete file at `$HOME/.claude/skills/karpathy-guidelines/SKILL.md`**, resolving `$HOME`
to the current user's home directory; this applies to Claude Code and Codex conductors alike. Read
it at session start, and again before resuming after any context compaction or reset, whether
automatic or through `/compact` or `/clear`. If the file is missing or unreadable, stop and report
the path and the error; do not conduct from memory. Apply all four principles to the orchestrator,
merged with project-specific instructions as needed. They are not restated here, so there is no
second copy to drift.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

The full four principles apply to the conductor only; worker briefs carry the worker-layer
version (a one-shot worker states its assumptions in its result instead of asking).

> Source: [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) (MIT), adapted. See `NOTICE`.

## Tests

From `multiagent/`:

    python3 -m unittest discover -s engine/tests
    python3 engine/policy_engine.py --root "$PWD" self-test    # --root must be absolute

## Editing the installation

Never edit `~/.claude` directly. Work in a clone of it: change, test and commit there, then
fast-forward `~/.claude` to that commit (`git merge --ff-only`) and run
`scripts/deploy-multiagent.sh global`, which copies the policy and the skill to `~/.multiagent`
for Windows-side hosts. Never edit a deployed copy.

## Where things go

General lessons about running this system go to `_shared/learnings.md`; project-specific ones to
`_local/learnings.md` (untracked). Open work lives in `OPEN_ITEMS.md`, decisions and their reasons
in `_shared/design-basis.md`, releases in `CHANGELOG.md`.

## Legacy: System A

These files describe the retired file-based System A. They are kept pending a separate retirement
decision and do not describe the current procedure: `_templates/{task,context,log,worker-brief,worker-result,task-folder}.md`,
`_shared/{orchestrator-rules,routing,system-invariants}.md`,
`_shared/adapters/{call_worker.sh,_run.py}`, `_shared/backends.json` and
`.claude/agents/claude-main.md`. Its operating rules are archived at `_archive/system-a/AGENTS.md`.
