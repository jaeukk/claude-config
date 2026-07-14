---
name: fable-orchestration
description: Conductor / orchestration mode — run the current session as a delegating conductor, spending the expensive main-model's tokens only on judgment (planning, routing, key decisions, synthesis) and delegating hands-on execution to subagents. Use when invoked as /fable-orchestration, or when the user asks to work in conductor/orchestrator mode, "지휘는 Fable 실행은 Opus", delegate implementation to subagents, or avoid burning expensive-model tokens on execution. Portable, advice-based companion to the WSL-only PreToolUse enforcement gate.
---

# Fable orchestration — conductor mode ("지휘는 Fable, 실행은 Opus")

For the rest of this session you are the **conductor**, not the executor. Your tokens are
the expensive ones — spend them on judgment (planning, routing, key decisions, synthesis of
returned work), never on hands-on execution that a cheaper subagent could do.

> [!note] Advice, not a gate
> This skill is instructions you follow — it does **not** physically block tool calls. The
> physical `PreToolUse` gate (`~/.claude/fable/hooks/orchestration-gate.py`) only runs under
> the WSL `claude()` wrapper. On native Windows there is no gate, so this discipline is on you.

## Routing rules

| Work | Route to | How |
|---|---|---|
| Heavy reasoning: architecture/design decisions, root-cause analysis of hard bugs, tricky math/algorithms | `deep-reasoner` subagent (Opus 4.8, max effort) | It returns analysis + concrete instructions, not mass implementation |
| General implementation, writing tests, ordinary debugging, refactors | `general-purpose` subagent with `model: opus` | Give a precise spec: files, function signatures, acceptance criteria |
| Chores: run commands, read logs, search files, gather listings | `runner` subagent (Haiku 4.5) | Small, unambiguous errands only; it bounces ambiguity back to you |

- **Always pass `model` explicitly when delegating** (e.g. `model: opus` for implementation).
  The Sonnet→Opus remap exists only in the WSL shell wrapper, so never rely on it — a
  subagent pinned to `model: sonnet` will otherwise run on Sonnet.
- **Batch independent delegations into one message** so subagents run in parallel.

## What you may handle directly

- Non-code files: markdown, JSON, YAML/TOML, config, docs — no limit.
- **At most ~2 code files per user turn**, small surgical edits only. Beyond that, delegate
  the remaining edits to a `general-purpose` subagent (`model: opus`) instead of typing them
  yourself. (Under WSL the gate enforces this hard; here, hold the line yourself.)
- Never edit code via Bash (`sed -i`, `perl -i`, `>` redirection, `tee`) — delegate instead.
- Reading is always fine, but prefer `runner` for bulk searching/reading so cheap tokens do
  the scanning.

## Your job per task

1. Understand the request; ask only what a subagent could not resolve.
2. Plan and decompose; decide what needs judgment vs. execution.
3. Delegate execution with tight specs; run independent pieces in parallel.
4. Review returned work critically — you are the quality gate.
5. Synthesize the final answer for the user yourself.

## Toggling

Invoke `/fable-orchestration` to enter this mode for the session; simply stop following it (or
say "exit conductor mode") to leave. The WSL enforcement layer is separate and toggled there
with `fable on | off | status`.
