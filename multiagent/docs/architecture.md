# Claude-led multi-agent architecture

Claude Code is the default application and the only conductor for an active task. The
default conductor binding is `claude-frontier` (`Fable5`). Codex participates through
bounded worker calls and may become conductor only after a user-approved, recorded
handoff.

The machine-readable policy is split intentionally:

- `roles.yaml`: stable, model-independent responsibilities and permissions.
- `bindings.yaml`: role-to-backend candidates and per-call effort.
- `backends.yaml`: replaceable host/model registry and operational capabilities.
- `routing.yaml`: task classification, fan-out, retry, and independence rules.
- `approvals.yaml`: conductor approvals versus actions requiring the user.
- `task.schema.json`: per-task contract shape.

The `.yaml` files use the JSON-compatible YAML subset, so the Python 3.14 standard
library can validate them without another dependency.

## Enforcement boundary

The project-local Claude `PreToolUse` hook reads the active task contract and denies
unplanned worker calls, shell-based mutation, and out-of-scope file writes. Codex is
launched through the policy engine in `read-only` mode. A Codex implementer therefore
returns a patch; direct Codex writes remain disabled while `writes_mediated` is false.

The upstream starter remains under `_shared/` and `_templates/`. This overlay is the
local authority when it is stricter than the upstream prose.

## Global policy home

The canonical policy and `orchestration` skill live in the host-global
`~/.multiagent/` directory. This project's `_multiagent/policy` path is a compatibility
link to that global policy; task contracts, leases, and worker results remain project
local under `_multiagent/tasks/`.

## Windows and WSL

The workspace is shared through the Windows-mounted project path. The Node launchers in
`engine/adapters/` select the installed Windows Python 3.14 and `codex.cmd` on Windows,
or `python3` and native `codex` inside WSL. The policy engine itself requires only the
standard library and runs on WSL's Python 3.12. Native WSL Codex is therefore a worker
host, not a Windows-command compatibility shim.

## Concurrency protocol

Each task owns `tasks/<task-id>/lease.json`. Lease creation is exclusive; only its owner
may append `events.ndjson`, change dispatch state, or synthesize results. A stale lease
may be replaced after its expiry. Recursive orchestration is forbidden. Bulk workers
operate on independent shards, use one result schema, and fan in to the conductor.
