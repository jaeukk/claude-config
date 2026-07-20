# Portable policy layout

The architecture separates stable responsibilities from replaceable products:

| File | Authority |
|---|---|
| `roles.yaml` | Model-independent purposes, permissions, required capabilities |
| `bindings.yaml` | Ordered role candidates and call-specific effort |
| `backends.yaml` | Host, concrete model, family, sandbox, write mediation, subagent support |
| `routing.yaml` | Classification, fan-out, retry, fan-in, independence |
| `approvals.yaml` | Conductor approvals versus user approvals |
| `task.schema.json` | Required task-contract fields |

## Stable roles

- `conductor`: plans, routes, approves bounded work, owns the lease, synthesizes.
- `implementer`: produces scoped changes or an applicable patch.
- `critic`: independently challenges correctness and scope.
- `verifier`: runs checks and records reproducible evidence.
- `bulk_worker`: processes one independent shard using the shared result schema.
- `runner`: performs a bounded mechanical lookup or command.

## Task lifecycle

1. Create `tasks/<id>/task.yaml` from the template.
2. Validate the policy and task; acquire `tasks/<id>/lease.json` exclusively.
3. Put the ID in `tasks/.active-task` only while the hook should enforce that task.
4. Set `dispatch.current_role`, then call one approved backend.
5. Store worker outputs separately and append events only as the lease owner.
6. Retry only transport, timeout, or rate-limit failures. Escalate deterministic errors;
   never silently drop a shard.
7. Run independent critic and verifier passes, synthesize, clear the active pointer, and
   release the lease.

Conductor ownership can change only through a user-approved event recorded in the task.
Recursive orchestration is disabled unless a separate, explicit bounded-fan-out policy
is added.
