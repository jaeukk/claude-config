---
name: orchestration
description: Claude-led conductor mode for routing model-independent roles across Claude Code and Codex with task contracts, approval tiers, independent review, bounded fan-out, and policy-enforced write scopes. Use when invoked as /orchestration, when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Orchestration

Operate as the single conductor for the active task. By default the conductor host is
Claude Code with the `claude-frontier` binding (currently Opus 5), but define work in
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

The machine-readable policy wins when it is stricter than prose. If no project policy
exists, use the global `~/.multiagent/policy` fallback (a deployed copy; the canonical
source is `~/.claude/multiagent/policy` in the config repo — fix things there and
redeploy, never edit the deployed copy). Keep task runtime files in a
project installation unless the user explicitly chooses a global task root. See
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
  independent shards, then fan in to the conductor. The Gemini fast tier (`agy-fast`) is
  a third pool member; Claude and Codex must both stay present.
- `verifier`: Codex standard tier is first choice; fall back to the Claude mid tier
  (currently Sonnet 5) when Codex authored the artifact.
- `runner`: Claude fast tier first, then Codex low tier, then `agy-fast`.

Effort belongs to the binding, not the backend registry. Note the enforcement asymmetry:
the engine passes `effort` to Codex only, so Claude-side effort comes from the worker
agent's frontmatter. Keep the two in sync.

## The Gemini family (`agy`)

The Antigravity CLI `agy` is registered alongside `claude-code` and `codex` as
`agy-multimodal` (Gemini Pro tier) and `agy-fast` (Gemini Flash tier), both
`family: gemini`. It exists to close a structural gap: with only two families, `critic` and
`verifier` each have exactly one different-family candidate, so one vendor outage leaves
independent review with no eligible backend at all. Gemini is the third candidate for both
roles, never the first — prefer Codex, then Claude, then Gemini.

Two limits to state plainly, because the registry entry can read as more than it is:

- **It is not automatic failover.** `resolve_binding` returns the first eligible candidate
  and performs no health check, so the third candidate is never reached on the normal
  path. During an outage the conductor must select it deliberately. What the registration
  buys is that such a choice *exists*, not that it happens by itself.
- **System B cannot dispatch it.** The engine's only dispatcher is `dispatch_codex`. The
  `agy-*` capability declarations presuppose execution through System A's
  `call_worker.sh`; they are not evidence that the policy engine can invoke `agy` itself.
  Likewise `effort` in the binding is declarative for `agy` — nothing transmits it, since
  the Gemini tier is carried by the model name instead.

Three rules govern it:

- **Gemini models only.** `agy` also serves `claude-*` and `gpt-oss-*`. Registering one of
  those under `family: gemini` would misreport the vendor and silently defeat the
  different-family independence check. The engine rejects any `host: agy` backend whose
  model does not start with `gemini-`.
- **Pin the model per call.** `agy` accepts `--model` and `--effort low|medium|high` as
  arguments, so the account-global `/model` setting is not authoritative. System A's
  dispatcher passes through any argument that is not `@brief`/`@brief_content`, so pinning
  is a data change in `args_template`, not a code change.
- **Inline the sources.** Headless `agy` times out at 300s when told to walk a directory or
  open many files. Inline the snippets it needs into the brief and tell it not to open
  files. A single image or PDF path is fine.

`agy` is read-only in this system: `write_mode: result-only`, isolated temp cwd, and the
conductor records its output. It supports `--json-schema` and `--output-format json`, which
is how a `bulk_worker` shard conforms to the shared result schema.

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
