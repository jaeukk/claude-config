---
name: orchestration
description: Claude-led conductor mode for routing model-independent roles across Claude Code and Codex with task contracts, approval tiers, independent review, bounded fan-out, and policy-enforced write scopes. Use when invoked as /orchestration, when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Orchestration

Operate as the policy-enforced conductor **only** on Claude Code with the `claude-frontier`
binding (currently Opus 5). This is a designed safety invariant, not merely a default: Codex
child dispatch is Codex-family-only, so a Codex conductor cannot supply a different-family
critic or verifier for a Codex-authored artifact. On every other host, operate advisory and do
not acquire the conductor lease.

Within that, define work in model-independent roles, so backends and models can be swapped
without rewriting the procedure.

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

**Before step 1, check you are allowed to conduct at all** — see "Conductor host support" below.
Only `claude-code` / `claude-frontier` is a policy-enforced conductor; on any other host, stop
here and operate advisory. Do not reach step 3 and take a lease you cannot validly hold.

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

## Host adapter contract

Roles and bindings are model-independent; **dispatch is not**. Concurrency, batching, context
forking, and model-override syntax are properties of the host the conductor runs on, not of the
role being filled. Resolve the role and backend first, then hand the call to the current host's
dispatch adapter. Never encode one host's API shape in a role, a binding, or this procedure.

Adapters declare their limits in `routing.yaml` under `conductor_adapters`. The number of
children that may be live at once is:

    min(of whichever of these are known)
      defaults.max_fanout
      adapter.max_active_children     -- omit when null
      slots the runtime reports free  -- omit when unreported

An unmeasured limit is **absent, not zero**: drop it from the comparison. Never pass `null`
into the `min` as a literal — it raises in Python and silently becomes `0` in JavaScript, which
would stall every dispatch.

`max_fanout` is a **policy ceiling** on simultaneous workers, enforced against
`dispatch.active_workers` — it is not a statement of host capacity and must not be lowered to
describe one. A `bulk_worker` job may hold more logical shards than the host can run at once;
the adapter schedules them in waves. `max_active_children: null` means unmeasured on that host —
fall back to `max_fanout` alone rather than guessing.

**Claude Code** — batch spawn is available: several dispatches in one message run concurrently.

**Codex** — no batch-spawn call exists; children go out one `spawn_agent` at a time, at most
three live (the conductor holds one of four slots), so "dispatch all in a single message" is not
implementable there. When passing `model` or `reasoning_effort`, a full-history fork is
rejected: use `fork_turns: "none"` (the deterministic default) or a bounded positive turn count
when the child genuinely needs recent context. The Codex child API exposes **Codex-family models
only** — it cannot dispatch a Claude or Gemini backend, so binding resolution on a Codex
conductor must filter to what its adapter can actually invoke, not merely on family and
capability.

### Conductor host support

Only **`claude-code` / `claude-frontier`** is a policy-enforced conductor: `task.schema.json`
pins both as constants and the validator rejects anything else. **This is deliberate.** Because
Codex child dispatch is Codex-family-only, a Codex conductor could never obtain the
different-family `critic` and `verifier` that `bindings.yaml` requires and `resolve_binding`
fails closed on — so widening the constants would accept contracts the runtime cannot fulfil.
Treat it as an invariant to preserve, not an unfinished feature.

A `conductor_adapters` entry does **not** make a host eligible to conduct; it describes dispatch
mechanics only, and Codex already has one. Codex conductorship becomes supportable only when its
adapter can invoke at least one non-Codex critic and verifier *and* binding resolution filters
candidates by adapter dispatchability as well as family independence.

On any other host, do **not** acquire a conductor lease or claim enforced orchestration — say
plainly that enforced orchestration is limited to Claude Code and why, then fall back to
advisory operation. Extending this
means adding a conductor backend, allowing it in the conductor binding, replacing the schema
constants with validated values, and removing the validator's hard-coded check — not asserting
a contract the engine will refuse.

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
two small files and send them through independent critic review. Write authority is decided
per selected backend and enforcement adapter, **not by product family**: a worker whose backend
declares `writes_mediated: false` returns results or patches even when its host is capable of
`workspace-write`. Direct writes need both an approved scope and an adapter that validates
mediated writes.

Treat that as the contract you must honour, not as something the engine checks for you.
`authorize_action()` currently keys on role and path only — it does not consult the selected
backend's `writes_mediated` / `write_mode`, and the validator merely *warns* for a read-only
backend. Today the registered Codex backends are safe only because their dispatcher is
hard-coded read-only. A future backend that advertises mediated writes would pass validation
without that protection, so the conductor, not the engine, is the thing keeping this true.

When no policy installation or enforcement adapter is available, apply the same rules
as advice and choose the more restrictive action.
