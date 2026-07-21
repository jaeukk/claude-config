# Final implementation plan

## Target state

Claude Code is the default application and Fable5 is the asserted conductor. One task
has one conductor and one lease. All delegated work uses model-independent roles;
bindings select replaceable Claude or Codex backends. `/orchestration` and its policy
are globally canonical under `~/.multiagent/`, with project-local task runtime state.

Python 3.14 and WSL were treated as existing prerequisites, not installation work.
Native Windows is the default execution path; Ubuntu 24.04 WSL is a validated second
host with the same policy and canonical skill.

## Completed implementation

1. Installed Claude Code globally and the `multi-agent-starter` Claude marketplace
   plugin; generated its Claude flavor under `_multiagent/`.
2. Installed the standalone Codex CLI and registered `codex.cmd mcp-server` in Claude
   Code's local scope for this workspace.
3. Added machine-readable roles, bindings, backend capabilities, routing, approvals,
   and task-contract schema as a stricter overlay over the upstream starter.
4. Added a Python 3.14 policy engine with binding resolution, scope authorization,
   exclusive leases, owned event append, native/WSL Codex dispatch, and self-tests.
5. Added Claude `PreToolUse` enforcement for scoped file tools, non-recursive worker
   calls, destructive-action approval, and a ban on shell-based mutation.
6. Revised `/orchestration` and made Claude and Codex use one canonical skill
   source through a directory junction. The former Codex copy was retained as a dated
   backup.
7. Validated the upstream starter, policy engine, hooks, skill package, Claude-to-Codex
   MCP connection, and a live read-only Codex Sol critic call.
8. Installed native user-local Codex in WSL, set WSL Claude Code's default to Fable 5,
   registered its native Codex MCP server, and archived the superseded WSL Fable gate.

## Installed plugins, agents, and runtimes

| Kind | Installed item | Purpose |
|---|---|---|
| Claude Code | `@anthropic-ai/claude-code` | Default conductor application |
| Codex CLI | `@openai/codex` | Native worker runtime and MCP server |
| Claude plugin | `multi-agent-starter@multi-agent-starter` | Upstream task/workspace scaffolding |
| Shared skill | `orchestration` | Conductor lifecycle and policy routing |
| Upstream agent | `claude-main` | Claude worker definition, high effort |
| MCP worker host | `codex` | Codex worker and critic access from Claude |

No email, calendar, storage, issue-tracker, or chat plugins are required for this
architecture. Add them only when a task actually needs their external capability.

## WSL host

The Ubuntu 24.04 WSL host uses `/home/jaeukk/.local/bin/codex` (native Linux Codex),
not the mounted Windows executable. Its Claude and Codex skill directories, plus
`~/.multiagent`, link to the canonical Windows global home at
`/mnt/c/Users/김재욱/.multiagent`. The legacy WSL Fable wrapper and gate are archived
under `~/.claude/legacy/`, with dated backups of `settings.json` and `.bashrc` beside
their originals. The migration is reproducible with
`engine/adapters/install_wsl_orchestration.js`.

## Role bindings

| Role | Binding |
|---|---|
| `conductor` | Claude frontier / Fable5, high, session assertion |
| `implementer` | Claude core high; Codex standard high alternative |
| `critic` | Codex high / Sol high; different-family Claude fallback |
| `bulk_worker` | Claude fast / Haiku low and Codex low in one shard pool |
| `verifier` | Different family from the implementer |
| `runner` | Claude fast or Codex low |

Codex direct writes remain disabled. Codex implementers return applicable patches in a
read-only sandbox until a future mediated-write adapter is separately threat-modeled,
tested against out-of-scope changes, and enabled in `backends.yaml`.

## Next controlled revision

The only deferred capability is direct Codex writing. Enable it only after adding an
isolated worktree/copy boundary, pre/post file inventory, atomic patch promotion,
out-of-scope rollback, and tests for symlinks, junctions, concurrent edits, and failure
recovery. This is a capability upgrade, not required for the current Claude-led system.
