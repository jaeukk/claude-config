# Multi-agent architecture

Claude Code is the default application for an active task, with the conductor binding
`claude-frontier` (`Opus 5`); Codex may also conduct as `codex-frontier`. Eligibility is
computed rather than pinned: a host may conduct when its backend is a declared `conductor`
candidate and its `conductor_adapters` entry declares `dispatch_hosts` reaching an
independent critic and verifier for every author family. Adding a conductor therefore means
giving a host a real cross-family dispatch path, not editing a constant. Note the remaining
asymmetry: contracts are *validated* on both hosts but *enforced* only on Claude Code, which
alone has a PreToolUse adapter. Conductor handoff still requires user approval (D12).

The machine-readable policy is split intentionally:

- `roles.yaml`: stable, model-independent responsibilities and permissions.
- `bindings.yaml`: role-to-backend candidates and per-call effort.
- `backends.yaml`: replaceable host/model registry and operational capabilities.
- `routing.yaml`: task classification, fan-out, retry, and independence rules.
- `approvals.yaml`: conductor approvals versus actions requiring the user.
`docs/task-contract.schema.json` describes the per-task contract shape, but nothing loads it:
`validate_task()` in `engine/policy_engine.py` is the only thing that checks a contract, and it
covers a subset. The schema sits in `docs/` to make that visible from its path.

The `.yaml` files use the JSON-compatible YAML subset, so the Python 3.14 standard
library can validate them without another dependency.

## Enforcement boundary

The project-local Claude `PreToolUse` hook reads the active task contract and denies
unplanned worker calls, shell-based mutation, and out-of-scope file writes. Codex is
launched through the policy engine in `read-only` mode. A Codex implementer therefore
returns a patch; direct Codex writes remain disabled while `writes_mediated` is false.

**Known gap — a returned patch has no sanctioned way to land.** `git apply` is denied by the
hook's shell-mutation rule, and conductor edits are capped at two code files by
`direct_conductor_edit.max_code_files` (code suffixes only; docs and config do not count), so
an authorized patch touching three or more code files cannot be applied while honoring the cap. A patch within the conductor's remaining two-code-file allowance can
still be applied with the ordinary file tools; what has no route is a patch that exceeds that
allowance or needs operations those tools do not perform. This is why `implementer` keeps
`claude-core` at rank 1: a Claude implementer can be spawned natively under the hook and write
directly, whereas `codex-core` (rank 2) runs read-only on every path — CLI `exec` and
the `mcp__codex__` tool alike — and can only return a patch. Any CLI-dispatched implementer of either family hits this gap. Until a narrow
`apply-worker-patch` operation exists — one binding an engine-recorded implementer dispatch to
a patch digest, requiring the live lease, and validating every affected path (renames and
symlink escapes included) against `write_scope` — implementation that must actually write
files should be spawned natively as a Claude subagent under the hook, not dispatched through
the CLI. That route exists only on Claude Code; a Codex conductor has no native Claude spawn
and must stop and request a conductor handoff (user approval) for such work. Do not close this
gap by relaxing the hook or zeroing the counter.

The upstream starter remains under `_shared/` and `_templates/`. This overlay is the
local authority when it is stricter than the upstream prose.

## Hosts and families

Two hosts serve workers: `claude-code` and `codex`, mapping to families `claude` and `codex`.
A third was registered for a reason worth restating, since it no longer applies: `critic` and
`verifier` bind with `different_family_from_author`, so a two-family registry leaves exactly
one eligible candidate per role, and one vendor outage halts independent review. It was never
automatic failover — `resolve_binding` returns the first eligible candidate without probing
health, so a third-ranked candidate had to be chosen deliberately during an outage.

`agy` is **disabled** (D14): no binding candidate, absent from every `dispatch_hosts`, and
its System A workers (`gemini`, `gemini-reader`) are removed from `_shared/backends.json`. The
`agy-multimodal`/`agy-fast` registry entries and the `_agy_cli` builder remain but are
unreachable, kept so re-enabling is configuration rather than a rewrite. The cost: with two
families, `critic` and `verifier` again have exactly one different-family candidate each.

`agy` backends must run Gemini models. The CLI also serves `claude-*` and `gpt-oss-*`
models; registering one of those under `family: gemini` would misreport the vendor and
defeat the independence check without raising an error, so `validate_policy` rejects any
`host: agy` backend whose model does not start with `gemini-`. That check still runs, though
nothing currently registers a reachable agy backend.

## Global policy home

The canonical policy and `conductor` skill (formerly `orchestration`) live in this repo,
`~/.claude/multiagent/`. The host-global `~/.multiagent/` directory and a project's
`_multiagent/policy` path are generated consumers, refreshed from the canonical repo by
`scripts/deploy-multiagent.sh` and never edited in place; task contracts, leases, and
worker results remain project local under `_multiagent/tasks/`.

## Windows and WSL

The workspace is shared through the Windows-mounted project path. The Node launchers in
`engine/adapters/` select the installed Windows Python 3.14 and `codex.cmd` on Windows,
or `python3` and native `codex` inside WSL. The policy engine itself requires only the
standard library and runs on WSL's Python 3.12. Native WSL Codex is therefore a worker
host, not a Windows-command compatibility shim.

## Concurrency protocol

Each task owns `tasks/<task-id>/lease.json`. Lease creation is exclusive, and the engine
lets only its owner append `events.ndjson`, claim worker slots, or release the lease. This
is a protocol among cooperating callers, not a filesystem boundary: any local process can
edit `task.yaml` or a result file directly, and nothing prevents it. A stale lease may be
replaced after its expiry. Recursive orchestration is forbidden. Bulk workers
operate on independent shards, use one result schema, and fan in to the conductor.
