---
name: multiagent
description: Conductor mode, runnable from Claude Code or Codex, for routing model-independent roles across Claude and Codex backends with task contracts, approval tiers, independent review, bounded fan-out, and validated write scopes. Use when invoked as /multiagent (formerly /orchestration, a name that now belongs to Orca's skill), when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Orchestration

Operate as the conductor on any host declared in the `conductor` binding — today Claude Code
(`claude-frontier`, currently Opus 5) and Codex (`codex-frontier`, currently Astra). The invariant being
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

One thing neither command checks: `claude-frontier`'s `model` pin must equal the model your
session is actually running. The conductor binding is a session assertion — the pin
*describes*, it does not select — so after `/model` changes the session, the contract asserts
something false until the pin and the two session levers (D10) follow it.

1. Decompose the request into the smallest useful role set: `implementer`, `critic`,
   `bulk_worker`, `verifier`, or `runner`.
2. Create and validate a task contract with explicit `target_repo`, `write_scope`,
   planned roles, approval state, and lease owner.
3. Acquire the task lease. Never run two conductors for one task.
4. Resolve each role through `bindings.yaml`; do not encode model names in a role or
   routing rule.
5. Set `dispatch.current_role` before each worker call, and choose how the result comes back:
   stdout for the conductor to use, or `--out <path>` for the engine to publish it (see
   "Accounts"). Workers must not spawn workers, widen scope, or synthesize the final answer.
6. Require critic and verifier families to differ from the artifact author.
7. Collect structured evidence, apply the retry classification, synthesize once, and
   release the lease.

## Tiers and required bindings

Backends are named `<family>-<tier>` for Claude and Codex, and each tier carries one role on
both families — except `fast`, which carries both `bulk_worker` and `runner`.
A tier is a capability rank, not an effort: `codex-frontier` runs at medium while `codex-core`
runs at high, because Astra at medium still out-reasons Sol at high.

| Tier | Role | Claude | Codex | Team backend |
|---|---|---|---|---|
| ceiling | critic | Fable 5.1, high | Astra, medium | — |
| frontier | conductor | Opus 5 (session assertion) | Astra, medium | — |
| core | implementer | Opus 5, high | Sol, high | `claude-core-team` |
| mid | verifier | Sonnet 5, medium | Terra, medium | `claude-mid-team` |
| fast | bulk_worker, runner | Haiku 4.5, low | Terra, low | `claude-fast-team` |

The team column is a *separate registry entry*, not a variant of the private one: nothing links
`claude-core` and `claude-core-team` but the naming convention, so a model change edits both.
`account` is orthogonal to tier — the tier is still the capability rank.

- `implementer`: every candidate at high effort (the validator enforces this). `claude-core-team`
  is rank 1 and a native spawn *cannot select it* — account-bound candidates are skipped for
  native spawns — so the ranking splits the two cases by construction: a native subagent gets
  `claude-core` and writes code under the hook, while a CLI dispatch gets Opus on the team
  account. That costs nothing, because a CLI-dispatched implementer was already text-only:
  `codex-core` and `claude-core-team` alike are read-only workers that return a patch nobody can
  land — see "What cannot land" below. Dispatch document production as `implementer`, not
  `bulk_worker`, when the writing quality matters.
- `critic`: ceiling tier, different family from the author. Claude-authored work gets
  `codex-ceiling`; Codex-authored work gets `claude-ceiling`.
- `verifier`: mid tier, different family from the author. Mind the CLI asymmetry under "What
  cannot execute".
- `bulk_worker`: both fast tiers in one pool; the validator requires both families present.
  `claude-fast-team` leads it — see "Accounts".
- `runner`: `codex-fast` first — on the recorded benchmark (`_shared/capability-profile.md`) it
  matched Claude's accuracy at 26× fewer tokens (9× fewer than Gemini) — then `claude-fast-team`,
  then `claude-fast`.

"First, then" is an ordered preference, not failover. `resolve_binding` returns the first
compatible candidate without probing health, and a worker that fails is reported, not retried
on the next candidate. During a Codex outage, reach the Claude fallback explicitly with
`--required-family claude`; retrying without it selects Codex again.

Effort belongs to the binding, not the backend registry, and `dispatch-worker` transmits it on
every CLI path: `-c model_reasoning_effort` for Codex, `--effort` for Claude. The one
place it is *not* transmitted is a Claude worker spawned natively as a subagent, which takes
effort from its agent frontmatter. Neither is the **model**: the hook checks only that a
compatible same-family binding exists, so a native spawn runs whatever the agent frontmatter or
session selects. Resolving `claude-ceiling` does not make a native subagent Fable 5.1 — only
its frontmatter does. Keep both model and effort in the frontmatter in sync with the binding,
and never report the resolved backend as the model that ran unless the frontmatter says so.

## Accounts

Two Claude logins exist here: **private** (`~/.claude`, the account this session runs on, the only
one with Fable) and **team** (`~/.claude-team`). The account is fixed per process, so:

- **A natively-spawned subagent always bills the session's own login.** There is no per-subagent
  account selector, and the hook's binding check does not change that. A native spawn is never
  team, whatever the binding resolves.
- **`dispatch-worker` is the only route to the team account.** It sets `CLAUDE_CONFIG_DIR` on the
  worker process. `claude-core-team`, `claude-mid-team` and `claude-fast-team` are the team-bound backends;
  `config_dir` in `backends.yaml` is what binds them. A backend in no binding is unreachable:
  `--required-family` filters a role's candidate list, it cannot summon a backend from outside it.
- **Prefer dispatch for `bulk_worker` and `runner`.** Spawn them natively only when a shard needs
  Bash inside its own loop; record why in the contract and say plainly that it billed the session
  account. Writing *code* is still private-only, since only a native subagent can write under the
  hook and a native spawn is never team (see "What cannot land").
- **Quota routing is not a health check.** Before selecting a team backend the engine probes that
  account: `available` routes there, `exhausted` (a window at or past 95%) falls to the private
  backend, and `unknown` — an unreadable probe, a rate-limited usage endpoint — **routes to team
  anyway**, because absence of evidence is not exhaustion and the real run reports the truth. One
  private retry follows a team run that comes back rate-limited; nothing else is retried.

### Publishing a worker's text: `--out`

A dispatched worker is read-only and always will be on this path. When its result is the
deliverable — a note, a summary, a review write-up — `dispatch-worker --out <path>` has the
**engine** write it, so the conductor never re-emits a long document through its own Write tool:

    policy_engine.py dispatch-worker --task … --role bulk_worker --brief … --out notes/paper-x.md

It refuses before launching anything (so a bad destination costs no tokens) when the suffix is
code, when the destination is reserved — engine state of any task, any `.git*` component,
`.claude`/`.codex`/`.vscode`, `.mcp.json` — when `authorize_action` denies it, or when the dispatch would be a
workspace dispatch — a role that may write, resolving to a Claude backend that is not
`result-only`. An account-bound backend is always `result-only` (the validator requires it), so
`implementer` on `claude-core-team` publishes normally while `implementer` on `claude-core`
is refused. On success it writes an immutable snapshot beside the
record, then the destination, then `outputs/<dispatch_id>.json` carrying account, backend, model,
attempt, sha256 and lease generation. A failed attempt records `status: failed` with a named reason
and writes nothing. Code never goes through `--out`; it needs a reviewed patch, which does not
exist yet.

A clean exit is not evidence of a usable document: a worker that declines a brief in one word
exits 0 with a well-formed envelope, and publishing that would overwrite a real note. So a
result under 200 bytes of non-whitespace is recorded as `below_min_bytes` and not published;
the text is still on stdout. Pass `--min-bytes` when the expected output is genuinely short
(`0` disables the floor).

### Landing a worker's files: `--write`

When the deliverable is files rather than one document, `dispatch-worker --write <path>` gives the
worker a real write path to **one** destination — a file, or an existing directory — inside
`target_repo` and inside the contract's `write_scope`:

    policy_engine.py dispatch-worker --task … --role implementer --brief … --write src/parser.py

Only a role with `may_write` may use it (today `implementer`), it cannot be combined with `--out`,
and it is native-host only. The worker runs `--restricted` with the file tools added and a
generated permission file naming that one destination: an unmatched path has no rule to approve it
and no handler to ask, so it is refused. Every generated rule is an `Edit` rule — measured
2026-09-18, a `Write(...)` rule authorizes nothing, so emitting one would read like a guard while
enforcing nothing. That is the boundary — not the prompt, and not the
conductor's trust. A malformed pattern would fail *open*, so destinations containing `*?[]{}!` are
refused rather than escaped.

Nothing under `~/.claude` can be a `--write` destination: the CLI gates those paths as
sensitive and asks a human, whatever the allowlist says, so a worker cannot edit this
installation. `--write` is also refused outright while a managed-settings file exists, because
managed settings merge into the worker's permissions and the generated allowlist would no longer
be the whole authority. User- and project-scope settings do not merge under `--restricted`
(measured 2026-09-18, with a control that applied the same grant without the flag).

**Reading outside the repository.** A worker's cwd is `target_repo` and nothing else is readable,
so a brief pointing at a PDF beside the task fails and the worker reports that as its own refusal.
A contract may declare `read_scope`, a list of absolute existing directories, which the engine
passes as `--add-dir`. Entries are canonicalized, must not be `$HOME` or a filesystem root, and are
refused if they are, contain, or sit inside a credential or agent-configuration directory. It is
Claude-only — a Codex worker's sandbox already reads the filesystem — and refused under
`--host wsl`. Note that `--add-dir` grants **write** as well as read, so a read root is writable
by a worker that also has `--write`.

Recovery is the engine's baseline, not git. A vault may gitignore the very layer the builds
write — `20_Notes/.gitignore` ignores `40_Resources/`, so `git status` and `git diff HEAD` see
nothing there and a retained-change test built on them is inert, not merely awkward. Where git is
blind, `find <dir> -newermt '<launch time>'` is a discovery aid — it misses deletions and anything
written with a preserved timestamp — and the engine's baseline comparison is the complete answer.

Before launch the engine copies the destination into `writes/<dispatch_id>.before` and verifies the
copy, so `restore-write --dispatch-id <id>` can put it back; a destination that changed after the
run was recorded refuses to restore rather than overwriting whoever changed it. Afterwards
`outputs/<dispatch_id>.json` carries the change set and one of `succeeded`, `succeeded_no_change`,
`partial` (the worker failed but files changed), `failed`, or `unknown` (the destination could not
be inspected — never reported as success). A rate-limited write attempt is **not** retried: the
retry rule assumes a repeat duplicates computation and never side effects, which stops being true
once files exist. Authorship is recorded whenever the change set is non-empty, including after a
failure — a failed attempt that changed files still authored those changes.

When `--write` produces a summary note, the brief must carry the note's frontmatter schema
(`citekey`, `zotero_key`, `tags`, `type`, `status`, `creator`, `Created`, …). A worker infers
none of it, and a batch of shards each guessing produces the per-note variance a critic then has
to find one field at a time.

`--out` changes nothing about the worker: same `--tools Read,Grep,Glob --strict-mcp-config`, same
prompt, and the worker is never told the destination.

### What a dispatched worker inherits

Nothing from your profile. Every CLI-dispatched Claude worker runs `--restricted`, which drops
user, project and local settings — so plugins, hooks and permission entries stay out of a worker
that never asked for them, and the input cost of a trivial dispatch roughly halves. It also drops
the global `CLAUDE.md`, which is replaced deliberately: the engine appends a composed baseline
(identity, American spelling, and that a one-shot worker states assumptions in its result instead
of asking). Everything else the old inheritance carried — vault layout, HPC schedulers, Zotero —
belongs in the brief that needs it. A worker cannot *run* `qsub`, but it can write a job script,
so "unreachable to execute" is not "irrelevant to author".

### Authorship must be evidenced, not assumed

A reviewer is cleared against what was *observed* producing the artifact. Two things record that:
the PreToolUse hook for a native producer, and `dispatch-worker` for a CLI one. The hook is wired
in `multiagent/.claude/settings.json` and loads only for a session launched from the installation
root — a session conducting from a vault never loads it, so its native producers leave no record.
The engine no longer papers over that: if the contract's `roles_plan` includes a producing role
and no authorship was *observed*, `critic` and `verifier` are **refused**, because "nothing was
recorded" and "the conductor wrote it" cannot be told apart otherwise. The conductor-is-author
fallback survives only for contracts that planned no producer at all.

A conductor's own account does not fill the gap. `record-author --task-dir … --family <family>
--source <what produced it>` stores an **assertion**, kept apart from observations: it can only
*add* families a reviewer must differ from, never stand in for the missing observation —
trusting it would reopen the bypass that rejecting `author_family` closed. Review is cleared on
assertions alone only when the user records `authorship_assertion` under `approvals.user`, the
same channel every other escalation uses, and every family that produced anything is asserted.
Be clear about what that channel is: the engine trusts `approvals.user` as the user's recorded
word everywhere, and it authenticates nobody — a conductor willing to fabricate an approval is
outside this model, as it is for every other escalation. Whether or not review is unblocked,
asserted families always *add* to the set a reviewer must differ from.
Remaining gaps, recorded rather than fixed: a native producer that was never named in
`roles_plan`, or removed from it afterwards, still reaches the fallback; the check reads the plan
as it stands, not as it ever was.

### Authorship accumulates

The sidecar records every family that produced part of the artifact, not just the last one. A
Claude `--write` over retained Codex output leaves both in the file, and a record that kept only
the latest writer would let a Codex critic review work its own family partly wrote. When more than
one family contributed, `critic` and `verifier` are **refused**: no candidate is independent of all
of it, so the artifact has to be split or reviewed by hand. Older single-family sidecars read as a
one-element list and mean exactly what they meant.

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
- **Native spawns count on the contract.** A Claude `Task` or Codex `spawn_agent` goes nowhere
  near the engine, so the hook can only read `dispatch.active_workers` — which the conductor
  writes itself. Keep it accurate; nothing else can.
- **Both counts charge one ceiling**, from whichever side asks: a CLI claim adds the
  contract's native count, and the hook's native check adds the lease's held slots.

A `bulk_worker` job may hold more logical shards than the host can run at once; the adapter
schedules them in waves. `max_active_children: null` means unmeasured on that host — fall back
to `max_fanout` alone rather than guessing.

What this does **not** do, so nobody mistakes it for more: it bounds *accidental* fan-out for
one cooperating conductor on one machine. It is not a security boundary. A `SIGKILL`ed
dispatcher leaves its child running and its slot held — until the lease expires unrenewed, at
which point the count is lost while the orphan may still be alive. A natively-spawned worker
is counted only because the conductor says so, and nothing stops a native spawn that never
took a lease at all. A process killed mid-update leaves `lease.lock` behind and wedges the
task: every later lease operation times out with a message naming the file, and lease expiry
will not clear it — stop the task's processes and remove it by hand. Anything needing to
survive a crashed or hostile participant needs a supervisor, not a JSON counter.

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
| `agy` | — | Disabled; not dispatchable. See "The Gemini family" below. |

An allowlist is not a substitute for `--tools`: `--allowedTools` only grants permissions and
leaves every other tool present. Never claim a Claude-hosted worker is sandboxed.

### Orca as transport

Orca's `/orchestration` launches workers as visible, persistent terminals but chooses
`--agent/--model/--effort` by hand and applies no policy. To keep the tier map and the
independence rule while using Orca's terminals, launch through the adapter instead of calling
`worker-start` directly:

    python3 engine/adapters/orca_worker_start.py --role critic --task <orca_task_id>

Run that command from the multi-agent installation root, or use the adapter's absolute path.
It attests the conductor from the current Orca terminal, rejects a contradictory
`--conductor-host` assertion, runs `resolve_binding` (family independence, tier, pinned model,
effort), and invokes `worker-start` with exactly that selection. Anything after `--` passes
through (`--name`, `--setup`, `--on`) except the flags the policy or recovery flow owns.
`--required-family` selects the outage fallback. The conductor role is session-only and is
refused here.

**What you lose crossing over — say it in every brief.** The engine's containment does not
apply: Orca launches Codex under *your* `~/.codex/config.toml` sandbox (`workspace-write`) and
Claude with *your* settings. A reviewer launched here can edit the thing it is reviewing. Tell
it not to, snapshot the target diff or hashes before launch, and compare them when it finishes;
`git status` alone cannot reliably detect edits to files that were already dirty.

**Authorship is a separate state store.** `tasks/orca/<run_id>/observed-author.json` is not
the engine contract's sidecar: nothing maps an Orca run to an engine task, so switching
transports carries no authorship automatically. The adapter serializes transitions with a
per-run lock and stores one bounded settled-author snapshot plus one active attempt:

- A producing or review launch reserves the run *before* `worker-start`, under a per-run
  lock, so adapter-launched attempts cannot overlap: a second tracked launch is refused until
  the exact task and dispatch are settled. The lock is per machine and covers only the
  adapter; a hand-typed `worker-start` is outside it.
- A successful producer is `--settle succeeded`. A failed producer that left changes is
  `--settle retained-output`, because failure does not erase authorship. Use
  `--settle no-output --confirm-no-output` only after verifying that no output remains.
- A completed critic or verifier is `--settle reviewed`. If the conductor then edits, use
  `--settle conductor-edited`; its family comes from Orca's attested terminal identity.
- A start Orca definitively **rejects** (`ok: false`, no dispatch — a bad flag, say) created
  nothing, so its reservation is released and the run is free again. A **lost or malformed**
  response is different: a worker may be live, so the attempt stays `outcome_unknown` and the
  run stays reserved. Inspect the saved request/dispatch; if the receipt preserved a request
  ID, `--resume-start --task <id>` replays the same Orca request instead of launching a
  duplicate; a *rejected* retry leaves the original attempt reserved, since its worker may
  be live. Otherwise an `outcome_unknown` attempt follows the no-recorded-dispatch rule
  below.
- An attempt with **no recorded dispatch** — `starting` (Orca never answered) or
  `outcome_unknown` (it answered unreadably) — is settled by the same rule either way. The
  reservation records, under the run lock, which dispatch the task already had. Such an attempt
  is settled only by the dispatch Orca reports as the task's *current* one — read under the
  same lock — and only if that differs from the baseline: the lock admits one adapter attempt
  at a time, so barring a hand-typed start or the residual below, nothing else could have
  created it. Settle it with that `--dispatch-id`, any outcome. The baseline itself is an
  earlier attempt's and is refused; so is any id that is not the current dispatch, however
  it was obtained. If nothing new appears and the
  reservation is over 600 s old, `--settle abandoned --task <id> --confirm-no-output` drops the
  attempt and keeps the settled author. 600 s is the adapter's own cap on its `worker-start`
  call, so an adapter-launched start older than that has returned or been killed; it is not a
  claim about Orca's internals. Every Orca query the adapter makes is capped at 60 s, so a hung
  Orca cannot hold the run lock. The residual none of this rules out: an Orca-side mutation
  still completing after its client died — Orca gives no way to ask about a request whose id
  was never received. Never delete the record by hand: that erases the
  settled author, and the next critic is then chosen against the conductor's family instead
  of the real producer's.
- With no producer record, the attested conductor is the author. An `--author-family` claim
  that disagrees with stored or attested evidence is refused.

**Audit-cycle procedure.** A procedural template: capture IDs from JSON rather than copying
placeholders. A Run must exist first (`run-create`, or the current terminal's bound Run); create a
fresh task for each cycle, since a completed task is already settled.

    <ORCA> orchestration task-create --spec <review-brief> --json
    python3 engine/adapters/orca_worker_start.py --role critic --task <task_id>
    # save <dispatch_id> from the start receipt (result.dispatchId), then loop:
    <ORCA> orchestration check --wait \
        --types worker_done,escalation,question --timeout-ms 900000 --json
    #   for EVERY message in the batch: answer a `question` with `orchestration reply`,
    #   handle an `escalation`. If a worker_done matches BOTH payload.taskId == <task_id>
    #   AND payload.dispatchId == <dispatch_id>, settle and release it BEFORE the ack --
    #   Orca's contract: decide each completed worker's fate before acknowledging, or a
    #   conductor that dies after the ack leaves the worker live with nothing to prompt
    #   its cleanup:
    python3 engine/adapters/orca_worker_start.py --settle reviewed \
        --task <task_id> --dispatch-id <dispatch_id>
    <ORCA> orchestration worker-release --dispatch <dispatch_id> --json
    #   then acknowledge the batch, matched or not -- an unacknowledged batch replays on
    #   the next wait, so a loop that acks only after the match stalls on any question:
    <ORCA> orchestration check --ack <delivery_id> --json
    #   repeat the wait until the matching worker_done was seen; a timeout or an
    #   unrelated batch is a checkpoint, not a failure.
    # apply findings; if the conductor edits, record it before the next cycle:
    python3 engine/adapters/orca_worker_start.py --settle conductor-edited --task <task_id>

Use the one Orca executable selected for the session (`ORCA_CLI_COMMAND`, then `orca-dev` in a
dev checkout, then `orca-ide` on Linux or `orca` elsewhere) everywhere `<ORCA>` appears. Orca
still checks none of this—a hand-typed `worker-start` silently bypasses policy—so the adapter
is the only sanctioned start path for a policy role. `--model/--effort` remain fresh-terminal
options and cannot combine with `--terminal`; the conductor is never launched.

### Conductor host support

A host may conduct when three things hold, all machine-checked:

1. Its backend is a declared candidate of the `conductor` binding (`claude-frontier`,
   `codex-frontier`).
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
  **heuristic** — an absolute path, a variable, or any interpreter defeats it. It catches the slip, not an
  adversary; do not grow the regex into something that looks authoritative.)
- **Fan-out of three.** `max_active_children: 3` caps simultaneous workers; schedule larger
  `bulk_worker` jobs in waves.

On a host with no conductor adapter at all, do **not** acquire a lease or claim enforced
orchestration. Say plainly which hosts are declared and why yours is not, then operate advisory.

## The Gemini family (`agy`) — disabled

`agy` is **off**. It holds no binding candidate and appears in no adapter's `dispatch_hosts`,
so no role can resolve to it and `--required-family gemini` returns "no compatible backend".
System A's `call_worker.sh` no longer defines `gemini` or `gemini-reader` either.

What remains, unreferenced: the `agy-multimodal` / `agy-fast` entries in `backends.yaml`, the
`_agy_cli` builder, and the engine's check that a `host: agy` backend must declare a Gemini
model. They are kept so re-enabling is a configuration change rather than a rewrite. Nothing
reaches them.

Know what turning it off costs, because it was registered for a reason. With two families,
`critic` and `verifier` each have exactly **one** different-family candidate, so a single
vendor outage leaves independent review with no eligible backend at all. Gemini was the third
candidate that closed that gap. It is now gone, and the gap is back.

Re-enabling needs all of: restore the binding candidates, add `agy` to `dispatch_hosts`, and
restore the System A workers — plus the two preconditions `_agy_cli` records (containment
actually established, a completion actually observed) and the argv-length work its docstring
names. Do not re-add it halfway.

## Two gaps the bindings do not tell you about

**What cannot land.** Still true for a *patch*; text lands through `--out` and files land through `--write`, since `--out` publishes a
worker's returned text through the engine (see "Accounts"). Every CLI-dispatched implementer —
either family — returns a patch.
`git apply` is denied by the hook's shell-mutation rule, so the only route is the conductor
applying it by hand with the file tools, and that is capped at two **code** files
(`CODE_SUFFIXES`; docs and config do not count). A patch touching three or more code files has
no route that honours the cap. On **Claude Code**, implementation that must write files goes to
a natively-spawned Claude subagent under the hook; use `codex-core` only when a patch *is* the
deliverable. On **Codex** there is no native Claude spawn, so such work is blocked: stop, say
so, and request a conductor handoff (user approval; `approvals.yaml`). Do not close this by
relaxing the hook or zeroing the counter; the fix is a
narrow `apply-worker-patch` operation (recorded dispatch, patch digest, live lease, every path
checked against `write_scope`), which does not exist yet.

**What cannot execute.** A CLI-dispatched Claude worker has no `Bash`, on purpose — granting it
would reopen the write path the tool restriction exists to close. So `claude-mid` as a verifier
can read tests but not run them, and that is exactly the verifier Codex-authored work resolves
to. When verification means running something: on Claude Code, spawn the verifier natively as a
Claude subagent under the hook. On Codex that route does not exist — either arrange for Claude
to be the author so the verifier is Codex (whose sandbox blocks writes but not commands), or
stop, report verification as blocked, and request a conductor handoff (user approval;
`approvals.yaml`). Never report a read-only review as executed verification.

## Authorship is observed, not declared

Independence is checked against `observed-author.json` beside the contract. `dispatch-worker`
writes it for CLI producers **only after the worker exits 0**, so a failed CLI run cannot
overwrite the real author's record. That write is guarded like every other engine state write —
lease generation and contract ownership rechecked under the lock, destination resolved so a
symlink cannot stand where the sidecar belongs — and it is skipped with a warning rather than
forced if the lease moved mid-run. A `--out` publication additionally records its own producer in
`outputs/<dispatch_id>.json`, so an artifact's author is known without widening the task-wide
sidecar; that is what attributes a `runner` or `bulk_worker` publication. The hook writes it for native producers **before the call
runs** — a PreToolUse hook cannot see the outcome — so a native producer that fails still
overwrites the record with its intent. A reviewer dispatch is
refused when the sidecar contradicts `author_family`, and also when the sidecar exists but is
unreadable, malformed, or names an unknown family. A *missing* sidecar means no producing
worker ran: the conductor authored the artifact and its own family is used. Fix the contract, not the
sidecar — with one narrow exception. A *failed native* producer has already overwritten the
record with its intent. Restore the previous author **only if** it retained nothing: compare
against the state before the call with `git status` plus `git diff HEAD` (working tree *and*
index; plain `git diff` misses staged and untracked files), and confirm no other producer ran
in between. Keep the baseline in context or in an authorized file — redirection is denied. If the failed producer left *any* retained change, the record is
correct as it stands — those changes are its — and the artifact is now mixed-family. Last
recorded producer wins; a mixed-family artifact collapses to one, so say so in the review
brief rather than pretending the earlier author is the only one.

## Approval and enforcement

The conductor may approve bounded worker calls and transient retries inside the task
contract. Obtain explicit user approval for conductor handoff, scope expansion,
destructive actions, external side effects, credentials, or policy overrides.

Two things the hook does not cover, so do not lean on it for them. It is wired in
`multiagent/.claude/settings.json`, which means it gates a session **launched from the
installation root** and nothing else — not a session in another project, and not any worker
process. And engine-mediated writes (`--out`, the state records) never reach it: they are
authorized inside the engine against the reloaded contract and recorded there.

During an active task, use hook-visible file tools for writes. Do not mutate through
shell redirection or bulk shell commands. On a host without a PreToolUse adapter (Codex), the
same rule holds without a hook to catch you: call `policy_engine.py authorize` with the write
before making it, and keep every mutation inside `write_scope`. Keep direct conductor code edits to at most
two small files and send them through independent critic review. The cap reads
`direct_code_files` from the contract, and nothing increments it for you: bump it yourself
after each new code file, or the cap never fires. It is a bare count, not a file list — once it
reaches two, every further code-file edit is refused, including re-edits of a file already
counted, so finish each file before bumping. Write authority is decided
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

## Picture book

`references/picture-book.html` — the whole procedure in seven pictures for someone who has
never seen it: request, contract, baton, four helpers, no self-review, tiers by brain size,
synthesis. Open it in a browser. For humans, not for you.
