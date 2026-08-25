# Task contract operation

1. Copy `_templates/task.yaml` to `tasks/<task-id>/task.yaml` and fill absolute target,
   write scopes, worker roles, and the conducting session's lease owner.
2. Run `policy_engine.py validate-policy` and `validate-task`, then `acquire-lease`.
   `dispatch-worker` mechanically refuses without a live lease you own, since that is where
   it keeps the worker count. A natively-spawned worker is not stopped by anything: there,
   holding the lease first is the cooperating-conductor protocol, not an enforced gate.
3. Put only the task ID in `tasks/.active-task` while orchestration is active.
4. Before a worker call, set `dispatch.current_role`; `dispatch-worker` refuses a role that
   disagrees with it. Increment `active_workers` **only** for a natively-spawned worker
   (Claude `Task`, Codex `spawn_agent`) and decrement it when that worker finishes —
   `dispatch-worker` keeps its own count on the lease and would otherwise be counted twice.
   The two counts are charged against one ceiling from either side, but only the lease-side
   count is kept by the engine — the contract number is as accurate as you make it.
5. Store each worker result separately. The conductor alone appends task events and
   synthesizes the final result.
6. Run critic and verifier with a model family different from the artifact author.
7. Release the lease, remove `.active-task`, and mark the task complete.

Only transport, timeout, and rate-limit failures are retryable. Invalid input, policy
denials, and deterministic failures must be surfaced to the conductor. No shard may be
silently dropped.
