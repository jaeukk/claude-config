---
name: multiagent
description: Conductor mode for Claude Code or Codex. The default is a single session; this skill adds the multiagent engine (task contracts, leases, independent review within an audit budget) only for cross-vendor review of consequential or untestable work, team-account routing, and contained writes or publication with a record. Use when invoked as /multiagent (formerly /orchestration, a name that now belongs to Orca's skill), when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Multiagent

## Scope: a single session first

The default is **one producer and no review loop**: this session, a native subagent (Claude
`Agent`, Codex `spawn_agent`; no contract), or one team-account worker with Bash
(`dispatch-worker --write <dest> --exec`). Measured 2026-09-29/30 on two test-oracle code tasks: the
multiagent arm scored within noise of a single session at 2–3.7× its cost, mostly because its
implementer lacked Bash; one critic → fix round changed the score in 1 of 7 runs. Which account
and model does the work is a separate choice: see "Accounts" and "Native spawns".

Reach for the engine only for:

- **(a) cross-vendor review** of consequential work, or of work with no test oracle
  (`audit_cycles` ≥ 1);
- **(b) team-account routing**: `dispatch-worker` is how a producer bills the team login with a
  record;
- **(c) contained writes or publication with a record**: `--write` (baseline, change set, restore)
  and `--out`.

With none of these planned, create no contract, lease or `.active-task`: do the work in this
session, even when the request names a task ID. The ceremony costs turns (bench8: 5–12 extra tool
calls per run at audit 0).

**One review round of work this session produced** (or its family's team workers did) is one call,
in the foreground:

    python3 $E review --conductor-host claude-code --target-repo <abs> --brief <file> \
        --out <findings.md> [--review-copy]        # a Codex conductor passes --conductor-host codex

It writes a contract (one critic, `audit_cycles` 1, the author is this session's family), takes the
lease, runs a reviewer of the other family, publishes its findings to `--out`, and releases the
lease. Put the agreed review definitions (see "Review discipline") in the brief. Use the full
procedure for work another family authored, several rounds on one contract, or a native reviewer.

A PreToolUse hook enforces the contract on Claude Code (sessions launched from `multiagent/`) and
on Codex (WSL `~/.codex`; sessions whose working directory is inside `multiagent/`), only while a
task is active.
Elsewhere the contract is validated and kept, not enforced: say **policy-validated**.

## Load the local authority

Policy is `multiagent/policy/{roles,bindings,backends,routing,approvals}.yaml`. The
engine reads it from its own tree (`--root`, default: the installation holding
`policy_engine.py`), never from `~/.multiagent`. Machine-readable policy wins over prose. Fix it in
a clone of `~/.claude`, never in a deployed copy (`multiagent/AGENTS.md`). Contract fields and
lifecycle: `multiagent/docs/task-contract.md`.

## Conductor procedure

Run from `multiagent/`, with `E=engine/policy_engine.py`.

1. `python3 $E validate-policy`; draft `tasks/<id>/task.yaml` from `_templates/task.yaml`;
   `python3 $E validate-task --task tasks/<id>/task.yaml`. Neither calls the other: the first
   checks the installation, the second whether this host may conduct and dispatch every planned
   role. The template and `docs/task-contract.md` are the reference: do not open other tasks'
   folders for examples, since they belong to other work and may hold material this task must not
   see. A task ID is lowercase (`[a-z0-9][a-z0-9._-]*`).
2. The contract names an absolute `target_repo`, `write_scope`, `roles_plan`, `audit_cycles`,
   `author_family` when review is planned, and `conductor.lease_owner`.
3. `python3 $E acquire-lease --task-dir tasks/<id> --owner <lease_owner>`. One conductor per
   task. Options cannot be abbreviated, and `acquire-lease` and `append-event` refuse a folder with
   no `task.yaml`.
4. For the hook to enforce the task, put its ID in `tasks/.active-task`.
5. `python3 $E dispatch-worker --task tasks/<id>/task.yaml --role <role> --brief <file>`, plus
   `--write` or `--out` (`references/workers.md`). `--dry-run` prints the resolved backend, account and
   `enforcement` without launching; for `--write`, read `write.enforcement`, not the
   base argv it shows. Before a native spawn under an active contract, set
   `dispatch.current_role` to that role: the hook authorizes the spawn against it. Workers do not
   spawn workers, widen scope, or synthesize the final answer. Never end your turn while a dispatch
   you started is still running: run `dispatch-worker` in the foreground, or wait for a
   backgrounded one to finish before you answer. A headless session (`claude -p`, `codex exec`)
   ends with its turn and kills the worker; the review is lost and its usage goes unrecorded.
6. Critic and verifier differ in family from the artifact's author (`references/authorship.md`).
7. Retry only transport, timeout and rate-limit failures; surface the rest; never drop a shard
   silently. Synthesize once, then `release-lease`: it also removes `tasks/.active-task` when that
   names this task, which you cannot do yourself while the hook enforces it. Then mark the task
   complete.

**Audit budget.** `audit_cycles` is how many critic rounds the task may run. Absent means 0, and
0 means no audit: the default. Set it from the user's instruction ("up to three audit cycles" is
3), never above it; raising it is the user's call. A round is one critic dispatch whose attempt
succeeded; a failed or rate-limited attempt spends nothing. `dispatch-worker` refuses a critic,
dry runs included, while the budget is 0 or spent, and `validate-task` rejects a planned critic
with a zero budget. The hook refuses a native critic at 0 but cannot count native rounds, so keep
a native loop within budget yourself. The verifier is not budgeted. Nothing forces review of
conductor code edits; set `audit_cycles` when they need it.

**Review discipline.** Before the first critic round, agree four things with the user and put them
in every critic brief: the threat model and scope, with the failure classes accepted up front; what
blocks landing (a realistic trigger, such as ordinary use, a crash, one interrupt or a supported
platform's normal behavior, plus its impact; anything else goes to the target project's open-items
list, `OPEN_ITEMS.md` for multiagent); the round plan (round 1 reviews the whole change, later
rounds only the fix diff) and budget; and the stop rule (by default, land after a clean round if the
user agreed to that; stop and report when the budget is spent or every blocking finding is in code
written that same round). Self-review the diff before each critic round (path ownership for every
create, replace and delete; every exit path, interrupts included; Windows vs POSIX; mutation checks
on new tests); it is not a critic dispatch and spends no `audit_cycles`. Triage each finding as fix,
defer or reject with a reason, and prefer deleting mechanism to adding it. A round that leaves
nothing to fix (no findings, or every one deferred or rejected) ends there: dispatch no fixer. Three
empty Codex reviews on 2026-09-30 each still paid for an Opus fix (about $0.41) that changed
nothing. Without this, an open-ended audit keeps finding ever-smaller issues: multiagent 1.5.0 took
12 rounds of findings.

## Tiers and bindings

Backends are named `<family>-<tier>`. A tier is a routing role, not an effort level.

| Tier | Role | Claude | Codex | Team backend |
|---|---|---|---|---|
| ceiling | critic | Fable 5.1, high | Astra, medium | — |
| frontier | conductor | Opus 5.5, high (session assertion) | Astra, medium | — |
| core | implementer | Opus 5.5, high | GPT-6 Sol, high | `claude-core-team` |
| mid | verifier | Sonnet 5.5, medium | Terra, medium | `claude-mid-team` |
| fast | bulk_worker, runner | Haiku 4.5, low | Terra, low | `claude-fast-team` |

- `implementer`: `claude-core-team`, `claude-core`, `codex-core`, all at high effort (the
  validator enforces it). A native spawn skips account-bound candidates, so it gets `claude-core`.
- `critic`: the other family's ceiling. `verifier`: `codex-mid` for Claude-authored work;
  `claude-mid-team`, then `claude-mid`, for Codex-authored work.
- `bulk_worker`: one pool led by `claude-fast-team`; the validator requires both families in it.
- `runner`: `codex-fast` first (cheapest at equal accuracy, `_shared/capability-profile.md`), then `claude-fast-team`, then `claude-fast`.

"First, then" is preference, not failover: `resolve_binding` returns the first compatible
candidate without a health check, and a failed worker is reported, not retried on the next one.
During a Codex outage, pass `--required-family claude`.

`dispatch-worker` transmits effort on every CLI path. A native Claude spawn takes model and effort from its agent frontmatter,
not from the binding; the hook checks only that a compatible binding exists. Keep the frontmatter
in sync, and never report the resolved backend as the model that ran unless the frontmatter says
so. Model evidence: `references/model-refresh-2026-09-27.md`.

**Native spawns: choose the type and model by tier.** A `general-purpose` subagent with no
`model` runs at the session's model (Opus or Fable) on the private login. Use `Explore` or
`runner` (Haiku, no edit tools) for lookups and searches; pass `model: "haiku"` for mechanical shards
and `model: "sonnet"` for routine production; keep the session's model only for judgment-heavy
work. On Codex, set `model` and `reasoning_effort` on `spawn_agent` (with `fork_turns: "none"`).

## Accounts

Two Claude logins: **private** (`~/.claude`, this session's, the only one with Fable) and
**team** (`~/.claude-team`). The account is fixed per process:

- A native subagent always bills the session's login; no binding changes that.
- The routes to team are `dispatch-worker` (it sets `CLAUDE_CONFIG_DIR` for the `*-team`
  backends), the `claude-worker` CLI (`~/.local/bin/claude-worker`, no contract or record), and
  the headless team driver (`references/workers.md`).
- Quota routing is not a health check. Before selecting a team backend the engine probes that
  account: `available` routes there, `exhausted` (a window at or past 95%) falls to private, and
  `unknown` (an unreadable or rate-limited probe) routes to team anyway. A team run that comes
  back rate-limited gets one private retry, never in write mode; nothing else is retried.
  `claude-worker` routes the same way.
- **When to push a job to team.** Hand it to one team producer when it is self-contained (the
  brief and readable files suffice; one destination), substantial (minutes of work, not a quick
  edit), and needs neither this conversation's context nor Fable. Keep it here when it is short,
  iterates with the user, or depends on what this session has loaded: moving it costs a brief and a
  cold start. One call runs the whole route:

      python3 $E produce --target-repo <abs> --brief <file> --write <dest> --exec   # or --out <path>

  It writes a contract (one planned producer, `audit_cycles` 0) under `tasks/<id>`, takes and
  releases the lease, dispatches, and prints the account, model and cost on stderr. Use the full
  procedure for review, several producers, or a contract you need to edit.
- **Cost records.** Every `worker_attempt` event carries account, model, outcome, and what the CLI
  reported (Claude `usage` and `total_cost_usd`, Codex `tokens_used`). `python3 $E cost-report
  [--tasks-root <dir>]... [--since <date>]` sums them by account and model; `uncosted` counts
  attempts with no cost data (recorded before 1.4.0, or by a driver that passed none). Such records are accounting only and never count as an audit round. A headless driver
  records each run with `python3 $E
  record-attempt --task-dir … --event '<json>'` (`account`, `model`, `classification` required).
  Native subagents leave no record.

## Reference (read when needed)

- `references/workers.md`: `--out` publication, `--write` and `--exec`, the headless team driver,
  and what a dispatched worker inherits. Read it before dispatching a worker that publishes text or
  writes files.
- `references/authorship.md`: how a critic or verifier is cleared against the artifact's authors.
  Read it when a reviewer is refused, or when more than one family produced the artifact.
- `references/hosts.md`: Claude Code and Codex spawn limits, which hosts may conduct and how each
  is enforced, and what cannot land or execute (`--review-copy`). Read it when conducting from
  Codex, or when a reviewer must run tests.
- `references/model-refresh-2026-09-27.md`: the model evidence behind the tier table.

## Approval and enforcement

The conductor may approve bounded worker calls and transient retries inside the contract. Get
explicit user approval, recorded under `approvals.user`, for conductor handoff, scope expansion,
destructive actions, external side effects, credentials, or policy overrides.

The hook is wired in `multiagent/.claude/settings.json` (matcher
`Edit|Write|NotebookEdit|Bash|Task|Agent|mcp__codex__.*`). It loads only in a session launched from
`multiagent/`, acts only while `tasks/.active-task` exists, and fails closed. It checks writes
against `write_scope`, denies shell mutation, requires approval for destructive commands, and checks
native spawns (planned role, family, audit budget). It gates no worker process, and
engine-mediated writes never reach it; the engine authorizes those itself.

During an active task, write with file tools, not shell redirection or bulk shell commands: inside
`write_scope`, or ordinary files in the task's own folder (briefs, notes, results). Engine state
there (the contract, lease, events, authorship, state trees and dot-files) stays refused.
`rm`, `mv`, `cp`, `tee`, `sed -i` and redirects into files are refused. `2>&1`, `>/dev/null` and a
quoted `>` are fine. `release-lease` clears `.active-task`; edit `task.yaml` after that. Without
a hook (Codex), call `authorize` before a write and keep every mutation inside `write_scope`.
