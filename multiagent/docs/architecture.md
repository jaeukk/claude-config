# Claude-led multi-agent architecture

Claude Code is the default application and the only conductor for an active task. The
default conductor binding is `claude-frontier` (`Opus 5`). Codex participates through
bounded worker calls; conductor handoff to another host is not currently supported —
`task.schema.json` pins the conductor host and backend as constants and the validator
enforces them, so a handoff needs a conductor backend, a schema change, and a validator
change, not just user approval.

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

## Hosts and families

Three hosts serve workers: `claude-code`, `codex`, and `agy` (the Antigravity CLI), mapping
to families `claude`, `codex`, and `gemini`. The third family is not redundancy for its own
sake — `critic` and `verifier` bind with `different_family_from_author`, so a two-family
registry leaves exactly one eligible candidate per role and one vendor outage halts
independent review.

This is availability of a *choice*, not automatic failover: `resolve_binding` returns the
first eligible candidate without probing health, so the third-ranked Gemini candidate is
never selected on the normal path and must be chosen deliberately during an outage.
`agy` also has no dispatcher in the engine — `dispatch_codex` is the only one — so its
declared capabilities presuppose execution through System A's `call_worker.sh`, and the
`effort` recorded in its bindings is declarative only.

`agy` backends must run Gemini models. The CLI also serves `claude-*` and `gpt-oss-*`
models; registering one of those under `family: gemini` would misreport the vendor and
defeat the independence check without raising an error, so `validate_policy` rejects any
`host: agy` backend whose model does not start with `gemini-`. `agy` is read-only
(`write_mode: result-only`) and, unlike Codex, has no dispatch path in the engine yet —
System A's `_shared/adapters/call_worker.sh` remains its dispatcher.

## Global policy home

The canonical policy and `orchestration` skill live in this repo,
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

Each task owns `tasks/<task-id>/lease.json`. Lease creation is exclusive; only its owner
may append `events.ndjson`, change dispatch state, or synthesize results. A stale lease
may be replaced after its expiry. Recursive orchestration is forbidden. Bulk workers
operate on independent shards, use one result schema, and fan in to the conductor.
