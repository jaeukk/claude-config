# Multi-agent architecture

Claude Code is the default application for an active task, with the conductor binding
`claude-frontier` (`Opus 5`); Codex may also conduct as `codex-conductor`. Eligibility is
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
`agy` has a builder in `WORKER_CLI`, but it is **withheld from every adapter's
`dispatch_hosts`**, so no engine-resolved role can reach it and System A's
`_shared/adapters/call_worker.sh` remains its only dispatcher. Two things must hold before it
is re-added: containment must be established (`--sandbox` restricts the terminal, not the
filesystem, so a throwaway cwd does not stop an absolute-path write), and a completion must
actually be observed through the engine path. Re-adding it then needs more than the
`routing.yaml` line: the prompt travels as one argv element, against a 32,767-character
Windows command-line ceiling, so prompt transport must move to stdin or a file first.

`agy` backends must run Gemini models. The CLI also serves `claude-*` and `gpt-oss-*`
models; registering one of those under `family: gemini` would misreport the vendor and
defeat the independence check without raising an error, so `validate_policy` rejects any
`host: agy` backend whose model does not start with `gemini-`. `agy` is expected to return
results rather than write (`write_mode: result-only`), but its `sandbox` field reads
`containment-unverified`: that expectation is not enforced by anything.

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

Each task owns `tasks/<task-id>/lease.json`. Lease creation is exclusive, and the engine
lets only its owner append `events.ndjson`, claim worker slots, or release the lease. This
is a protocol among cooperating callers, not a filesystem boundary: any local process can
edit `task.yaml` or a result file directly, and nothing prevents it. A stale lease may be
replaced after its expiry. Recursive orchestration is forbidden. Bulk workers
operate on independent shards, use one result schema, and fan in to the conductor.
