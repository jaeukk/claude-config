---
name: orchestration
description: Conductor mode, runnable from Claude Code or Codex, for routing model-independent roles across Claude, Codex, and Gemini backends with task contracts, approval tiers, independent review, bounded fan-out, and validated write scopes. Use when invoked as /orchestration, when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Orchestration

Operate as the conductor on any host declared in the `conductor` binding — today Claude Code
(`claude-frontier`, currently Opus 5) and Codex (`codex-conductor`). The invariant being
protected is not "Claude conducts": it is that **a host may conduct only if it can actually
reach a different-family critic and verifier**. That is a property of the host's dispatch
adapter, not of its vendor, and the engine checks it directly instead of trusting a hard-coded
name. On a host with no conductor adapter, operate advisory and do not acquire the lease.

Say **policy-validated**, not policy-enforced. The contract is validated on every host; it is
*enforced* only on Claude Code, where a PreToolUse hook can actually refuse a tool call. On
Codex nothing intercepts you — see "Conductor host support".

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
Run `policy_engine.py validate-policy` **and** `validate-task` on your draft contract — the two
answer different questions and neither calls the other. `validate-policy` checks the
installation (can each declared conductor reach an independent critic at all?); `validate-task`
checks your contract (is this host a declared conductor, can it dispatch every role you
planned?). Do not reach step 3 and take a lease you cannot validly hold.

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

Effort belongs to the binding, not the backend registry, and `dispatch-worker` transmits it on
every CLI path: `-c model_reasoning_effort` for Codex, `--effort` for Claude and `agy`. The one
place it is *not* transmitted is a Claude worker spawned natively as a subagent, which takes
effort from its agent frontmatter — keep that frontmatter in sync with the binding.

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

`max_fanout` is a **policy ceiling** on simultaneous workers — it is not a statement of host
capacity and must not be lowered to describe one. It is counted in two places, and only one of
them is trustworthy:

- **`dispatch-worker` counts on the lease.** The engine claims a slot before launching and
  releases it in a `finally`. Every lease mutation — acquire, heartbeat, claim, release —
  runs under one lock file and writes atomically, so parallel dispatches cannot lose an
  increment or read a half-written lease. This is why a real dispatch requires a live lease
  you own: no lease, no place to keep the count. The lease is heartbeaten for the worker's
  lifetime, bound to the lease generation so an abandoned dispatcher cannot prop up a lease
  it no longer belongs to.
- **Both counts charge one ceiling**, from whichever side asks: a CLI claim adds the
  contract's native count, and the hook's native check adds the lease's held slots.

What this does **not** do, so nobody mistakes it for more: it bounds *accidental* fan-out for
one cooperating conductor on one machine. It is not a security boundary. A `SIGKILL`ed
dispatcher leaves its child running and its slot held — until the lease expires unrenewed, at
which point the count is lost while the orphan may still be alive. A natively-spawned worker
is counted only because the conductor says so, and nothing stops a native spawn that never
took a lease at all. A process killed mid-update leaves `lease.lock` behind and wedges the
task: every later lease operation times out with a message naming the file, and lease expiry
will not clear it — stop the task's processes and remove it by hand. Anything needing to
survive a crashed or hostile participant needs a supervisor, not a JSON counter.
- **Native spawns count on the contract.** A Claude `Task` or Codex `spawn_agent` goes nowhere
  near the engine, so the hook can only read `dispatch.active_workers` — which the conductor
  writes itself. Keep it accurate; nothing else can. A `bulk_worker` job may hold more logical shards than the host can run at once;
the adapter schedules them in waves. `max_active_children: null` means unmeasured on that host —
fall back to `max_fanout` alone rather than guessing.

**Claude Code** — batch spawn is available: several dispatches in one message run concurrently.

**Codex** — no batch-spawn call exists; children go out one `spawn_agent` at a time, at most
three live (the conductor holds one of four slots), so "dispatch all in a single message" is not
implementable there. When passing `model` or `reasoning_effort`, a full-history fork is
rejected: use `fork_turns: "none"` (the deterministic default) or a bounded positive turn count
when the child genuinely needs recent context.

The Codex **child API** exposes Codex-family models only. That is a limit of one dispatch
mechanism, not of the host: `codex`, `claude`, and `agy` are all ordinary CLIs, so a Codex
conductor dispatches a Claude critic as a subprocess instead. Use
`policy_engine.py dispatch-worker --task … --role critic --brief …`, which resolves the binding
under your host, refuses anything your adapter cannot reach, and runs the resolved backend's CLI
with the brief on stdin. Never fill a cross-family role with `spawn_agent`; it cannot do it.

The dispatchers do **not** contain a worker equally well, and the dry run reports which you get
as `enforcement`:

| Host | Enforcement | What that actually means |
|---|---|---|
| `codex` | `os-sandbox-read-only` | The OS refuses the write. A real guarantee. |
| `claude-code` | `restricted-tool-surface` | `--tools Read,Grep,Glob` removes Bash and the write tools; `--strict-mcp-config` drops inherited MCP servers. Binds the agent, not the process — a settings-level hook could still act. |
| `agy` | `isolated-cwd-containment-unverified` | Not currently dispatchable — see "The Gemini family" below for why. |

An allowlist is not a substitute for `--tools`: `--allowedTools` only grants permissions and
leaves every other tool present. Never claim a Claude-hosted worker is sandboxed.

### Conductor host support

A host may conduct when three things hold, all machine-checked:

1. Its backend is a declared candidate of the `conductor` binding (`claude-frontier`,
   `codex-conductor`).
2. Its host has a `conductor_adapters` entry in `routing.yaml`.
3. That entry's `dispatch_hosts` reaches an independent `critic` and `verifier` for **every**
   author family — `validate_policy` rejects a conductor candidate that cannot, and
   `resolve_binding` skips any candidate the conducting adapter cannot invoke.

`dispatch_hosts` is required and **fails closed**: an adapter that omits it dispatches nothing.
So the way to keep a host out of the conductor seat is to withhold reachability, not to hard-code
a name — and the way to add one is to give it a genuine cross-family dispatch path, not to widen
an enum.

Two asymmetries remain real on Codex, and neither blocks conducting:

- **Nothing intercepts a Codex conductor.** `claude_pretool.py` gives Claude Code a
  PreToolUse gate on writes and on family independence; Codex has no equivalent adapter, so
  there **is no enforcement on Codex** — only what you choose to ask. Nothing stops a Codex
  conductor from filling `critic` with `spawn_agent` and reviewing its own artifact, and the
  Codex host's approval prompts do not help: they ask about side effects, not about which
  vendor is reviewing whom. So on Codex, call `policy_engine.py authorize` before acting and
  `dispatch-worker` for every worker, and describe the result as a contract you kept, never as
  a contract that was enforced. (Claude Code's gate is narrower than it sounds too: it sees
  tool calls, so a worker CLI launched from `Bash` bypasses the independence check. The hook
  denies what looks like one and redirects you to `dispatch-worker`, but that match is a
  **heuristic** — a leading space or an absolute path defeats it. It catches the slip, not an
  adversary; do not grow the regex into something that looks authoritative.)
- **Fan-out of three.** `max_active_children: 3` caps simultaneous workers; schedule larger
  `bulk_worker` jobs in waves.

On a host with no conductor adapter at all, do **not** acquire a lease or claim enforced
orchestration. Say plainly which hosts are declared and why yours is not, then operate advisory.

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
- **The engine has a builder for it, and still will not dispatch it.** `WORKER_CLI` knows how
  to launch `agy`, but `agy` is absent from every adapter's `dispatch_hosts`, so
  `resolve_binding` skips it and `--required-family gemini` returns "no compatible backend".
  That is deliberate, and the reason is not laziness: `--sandbox` restricts the terminal but
  is not a filesystem boundary, so a throwaway cwd does not stop an absolute-path write, and
  no completion has ever been observed through this path. Until containment is established
  *and* a run succeeds, Gemini is reachable only through System A's `call_worker.sh`.
  Re-enabling is *not* just the one line in `routing.yaml`: the prompt goes out as a single
  argv element, and Windows caps a command line at 32,767 characters, so an inlined brief of
  any real size needs moving to stdin or a file in the worker's cwd first. Routing change
  plus prompt-transport work, after both preconditions hold.

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

`agy` is *expected* to write nothing — `write_mode: result-only`, isolated temp cwd, and the
conductor records its output — but that is a convention, not a guarantee: its `sandbox` field
reads `containment-unverified` precisely because nothing stops an absolute-path write. It
supports `--json-schema` and `--output-format json`, which is how a `bulk_worker` shard
conforms to the shared result schema.

## Approval and enforcement

The conductor may approve bounded worker calls and transient retries inside the task
contract. Obtain explicit user approval for conductor handoff, scope expansion,
destructive actions, external side effects, credentials, or policy overrides.

During an active task, use hook-visible file tools for writes. Do not mutate through
shell redirection or bulk shell commands. On a host without a PreToolUse adapter (Codex), the
same rule holds without a hook to catch you: call `policy_engine.py authorize` with the write
before making it, and keep every mutation inside `write_scope`. Keep direct conductor code edits to at most
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
