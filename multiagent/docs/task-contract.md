# Task contract and policy layout

## Policy files

| File | Authority |
|---|---|
| `policy/roles.yaml` | Model-independent purposes, permissions, required capabilities |
| `policy/bindings.yaml` | Ordered role candidates and call-specific effort |
| `policy/backends.yaml` | Host, concrete model, family, account, sandbox |
| `policy/routing.yaml` | Fan-out ceiling and per-host dispatch reach (`conductor_adapters`) |
| `policy/approvals.yaml` | Conductor approvals versus user approvals |

The engine reads policy from its own tree (`--root`, default: the installation holding
`policy_engine.py`). `scripts/deploy-multiagent.sh global` copies `policy/` and the skill to
`~/.multiagent` for Windows-side hosts; the engine never reads that copy. Edit the canonical copy
in a clone of `~/.claude` only.

`docs/task-contract.schema.json` documents the contract's shape. Nothing loads it:
`validate_task()` is the only check that runs, and it covers a subset. Editing the schema changes
no behavior.

## Roles

- `conductor`: plans, routes, approves bounded work, owns the lease, synthesizes.
- `implementer`: produces scoped changes or an applicable patch.
- `critic`: independently challenges correctness and scope.
- `verifier`: runs checks and records reproducible evidence.
- `bulk_worker`: processes one independent shard.
- `runner`: performs a bounded mechanical lookup or command.

## Contract fields

Required: `schema_version`, `task_id`, `status`, `conductor` (`host`, `backend`, `lease_owner`),
`target_repo` (absolute), `write_scope`, `roles_plan`, `approvals` (`user`), `dispatch`.
Optional: `audit_cycles` (absent = 0, no audit), `author_family` (required when a critic or
verifier is planned), `read_scope`, `deviations`, `notes`.

`dispatch.current_role` is `null` or a planned role. `dispatch-worker` takes the role from
`--role`, but the Claude Code hook needs `current_role` set to the requested role before a native
spawn under an active contract: it authorizes and binds the spawn by it.

`dispatch.native_reason`, `direct_code_files` and `dispatch.active_workers` are no longer read.
Old contracts carrying them are accepted and ignored.

## Lifecycle

1. Copy `_templates/task.yaml` to `tasks/<task-id>/task.yaml` and fill the absolute target, write
   scope, planned roles, audit budget, and the conducting session's lease owner.
2. Run `policy_engine.py validate-policy` and `validate-task`, then
   `acquire-lease --task-dir tasks/<task-id> --owner <lease_owner>`. `acquire-lease`,
   `append-event` and `record-attempt` refuse a folder with no `task.yaml`. For a single
   producer with no review, `produce` runs steps 1–2 and the dispatch in one call. `dispatch-worker` refuses without a live lease you own,
   since that is where it counts CLI workers. A natively spawned worker is not stopped by a
   missing lease: there, holding the lease first is the cooperating-conductor protocol.
3. Put only the task ID in `tasks/.active-task` while the hook should enforce the task.
4. Before a native spawn, set `dispatch.current_role` to its role. Dispatch CLI workers with
   `dispatch-worker --role <role>`.
5. Store each worker result separately; only the lease owner appends events, except a headless
   driver's `record-attempt`, which is marked `source: external`. A CLI producer
   records its family in `observed-author.json` beside the contract (the hook records a native
   producer before it runs, success or not); reviewer dispatch is checked against that record,
   not against `author_family` alone.
6. Retry only transport, timeout, and rate-limit failures. Surface invalid input, policy denials
   and deterministic failures to the conductor; never silently drop a shard.
7. Run critic and verifier with a family different from the artifact author. A critic runs only
   within `audit_cycles` (N: at most N completed critic rounds), which `dispatch-worker`
   enforces.
8. Synthesize once, release the lease (this also clears `tasks/.active-task` when it names the
   task), and mark the task complete.

A host may conduct only when its backend is a `conductor` binding candidate and its
`conductor_adapters` entry declares `dispatch_hosts` reaching an independent critic and verifier
for every author family. `dispatch_hosts` fails closed: omit it and the adapter dispatches nothing.

Conductor ownership changes only through a user-approved handoff recorded in the task. Recursive
orchestration is forbidden.
