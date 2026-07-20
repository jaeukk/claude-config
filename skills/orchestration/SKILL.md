---
name: orchestration
description: Claude-led conductor mode for routing model-independent roles across Claude Code and Codex with task contracts, approval tiers, independent review, bounded fan-out, and policy-enforced write scopes. Use when invoked as /orchestration, when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Orchestration

Operate as the single conductor for the active task. By default the conductor host is
Claude Code with the `claude-frontier` binding (currently Fable5), but define work in
model-independent roles so hosts and models can be replaced later.

## Load the local authority

Find the nearest `_multiagent/` installation. If present, read all of these before
dispatching work:

- `policy/roles.yaml`
- `policy/bindings.yaml`
- `policy/backends.yaml`
- `policy/routing.yaml`
- `policy/approvals.yaml`
- the active `tasks/<task-id>/task.yaml`

The machine-readable policy wins when it is stricter than prose. See
`references/policy-layout.md` for the portable fallback and task lifecycle.

## Conductor procedure

1. Decompose the request into the smallest useful role set: `implementer`, `critic`,
   `bulk_worker`, `verifier`, or `runner`.
2. Create and validate a task contract with explicit `target_repo`, `write_scope`,
   planned roles, approval state, and lease owner.
3. Acquire the task lease. Never run two conductors for one task.
4. Resolve each role through `bindings.yaml`; do not encode model names in a role or
   routing rule.
5. Set `dispatch.current_role` before each worker call. Workers must not spawn workers,
   widen scope, or synthesize the final answer.
6. Require critic and verifier families to differ from the artifact author.
7. Collect structured evidence, apply the retry classification, synthesize once, and
   release the lease.

## Required bindings

- `implementer`: Claude and Codex candidates both run at high effort.
- `critic`: Codex high tier is first choice (currently Codex Sol); fall back to a
  different-family Claude backend when Codex authored the artifact.
- `bulk_worker`: use both Claude fast tier (currently Haiku) and Codex low tier for
  independent shards, then fan in to the conductor.

Effort belongs to the binding, not the backend registry.

## Approval and enforcement

The conductor may approve bounded worker calls and transient retries inside the task
contract. Obtain explicit user approval for conductor handoff, scope expansion,
destructive actions, external side effects, credentials, or policy overrides.

During an active task, use hook-visible file tools for writes. Do not mutate through
shell redirection or bulk shell commands. Keep direct conductor code edits to at most
two small files and send them through independent critic review. Codex must remain
read-only and return patches until its backend advertises validated mediated writes.

When no policy installation or enforcement adapter is available, apply the same rules
as advice and choose the more restrictive action.
