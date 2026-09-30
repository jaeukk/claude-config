# Hosts: adapters, conductor support, and two gaps

Moved verbatim from SKILL.md (2026-09-30). `$E` is `engine/policy_engine.py`, run from `multiagent/`.

## Host adapters

- **Claude Code:** several spawns in one message run concurrently.
- **Codex:** no batch spawn; one `spawn_agent` at a time, at most three live
  (`max_active_children: 3`). `fork_turns` defaults to `all`, and a full-history fork accepts no
  `model` or `reasoning_effort`; when setting either, pass `fork_turns: "none"` or a positive
  integer string.
- Codex's child API reaches Codex models only. Fill a cross-family role with `dispatch-worker`,
  which runs the resolved backend's CLI with the brief on stdin, never with `spawn_agent`.

The engine counts CLI workers on the lease against `min(max_fanout, max_active_children)`, so a
real dispatch needs a live lease you own. It bounds accidental fan-out; it is not a
security boundary. A process killed mid-update leaves
`tasks/<id>/lease.lock`: every later lease operation times out naming it, and expiry does not clear
it. Stop the task's processes and delete it by hand.

The dry run reports `enforcement`:

| Host | Enforcement | Meaning |
|---|---|---|
| `codex` | `os-sandbox-read-only` | The OS refuses writes. |
| `claude-code` | `restricted-tool-surface` | `--tools Read,Grep,Glob` removes Bash and the write tools; `--strict-mcp-config` drops MCP servers. Binds the agent, not the process. `--write` and `--exec` extend it (`write.enforcement`; the write record). |

## Conductor host support

A host may conduct when all three hold, each machine-checked:

1. Its backend is a candidate of the `conductor` binding (`claude-frontier`, `codex-frontier`).
2. Its host has a `conductor_adapters` entry in `routing.yaml`.
3. That entry's `dispatch_hosts` reaches an independent `critic` and `verifier` for every author
   family. `dispatch_hosts` fails closed: omit it and the adapter dispatches nothing.

**Codex.** `engine/adapters/codex_pretool.py`, registered in `~/.codex/hooks.json`, applies the
Claude hook's rules to Codex: shell commands, every file an `apply_patch` touches, and
`spawn_agent` children (as family `codex`, so a Codex child cannot review Codex work). It fails
open: Codex runs the call if the hook is untrusted, crashes or times out, and editing the hook
entry's command, matcher or timeout voids its trust silently until it is re-trusted (the hash is
in `codex app-server`'s `hooks/list`). Call `python3 $E authorize --task … --action '<json>'` for
anything the hook does not see.

**Claude Code.** The hook sees tool calls only. It denies what looks like a worker CLI typed into
Bash, but that match is a heuristic; an absolute path or a variable defeats it.

A host with no conductor adapter takes no lease and operates advisory.

`agy` (Gemini) is not wired; revive it from commit `8e57af8`. With two families, one vendor's
outage leaves `critic` and `verifier` with no independent candidate.

## Two gaps

**What cannot land.** A Codex implementer returns a patch, and nothing applies one:
`apply-worker-patch` does not exist, and the hook denies `git apply` during an active task. Apply
it by hand with file tools, or use a Claude implementer with `--write`.

**What cannot execute.** Among CLI-dispatched Claude workers, only a `--write --exec` producer gets
Bash; a Claude critic or verifier reads tests but cannot run them. A Codex worker can run commands,
but its default read-only sandbox blocks every write, temp files included, so a suite that needs a
temporary directory errors (4 of 4 Stage 1 critic runs). Add `--review-copy` to a Codex critic or
verifier dispatch: the engine copies `target_repo` (without `.git`, caches and virtual environments;
refused above 20,000 files or 500 MB) into a fresh temporary folder, runs Codex there with
`--sandbox workspace-write` (`/tmp` excluded, `TMPDIR` inside that folder), and deletes it
afterwards; the original stays read-only
(measured 2026-09-30: 281 tests ran, OK). Codex-authored work's verifier is `claude-mid-team`
first, which cannot run tests; for executed evidence there, run the suite yourself or spawn a native
Claude verifier under the hook. Report executed verification only for checks that actually ran.
