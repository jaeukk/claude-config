# Task contract operation

1. Copy `_templates/task.yaml` to `tasks/<task-id>/task.yaml` and fill absolute target,
   write scopes, worker roles, and the Claude session lease owner.
2. Run `policy_engine.py validate-task` and `acquire-lease`.
3. Put only the task ID in `tasks/.active-task` while orchestration is active.
4. Before a worker call, set `dispatch.current_role` and increment `active_workers`.
5. Store each worker result separately. The conductor alone appends task events and
   synthesizes the final result.
6. Run critic and verifier with a model family different from the artifact author.
7. Release the lease, remove `.active-task`, and mark the task complete.

Only transport, timeout, and rate-limit failures are retryable. Invalid input, policy
denials, and deterministic failures must be surfaced to the conductor. No shard may be
silently dropped.
