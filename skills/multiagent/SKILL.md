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

A PreToolUse hook enforces the contract on Claude Code (sessions launched from `multiagent/`) and
on Codex (sessions whose working directory is inside `multiagent/`), only while a task is active.
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
   role.
2. The contract names an absolute `target_repo`, `write_scope`, `roles_plan`, `audit_cycles`,
   `author_family` when review is planned, and `conductor.lease_owner`.
3. `python3 $E acquire-lease --task-dir tasks/<id> --owner <lease_owner>`. One conductor per
   task. Options cannot be abbreviated, and `acquire-lease` and `append-event` refuse a folder with
   no `task.yaml`.
4. For the hook to enforce the task, put its ID in `tasks/.active-task`.
5. `python3 $E dispatch-worker --task tasks/<id>/task.yaml --role <role> --brief <file>`, plus
   `--write` or `--out` (below). `--dry-run` prints the resolved backend, account and
   `enforcement` without launching; for `--write`, read `write.enforcement`, not the
   base argv it shows. Before a native spawn under an active contract, set
   `dispatch.current_role` to that role: the hook authorizes the spawn against it. Workers do not
   spawn workers, widen scope, or synthesize the final answer.
6. Critic and verifier differ in family from the artifact's author (see "Authorship").
7. Retry only transport, timeout and rate-limit failures; surface the rest; never drop a shard
   silently. Synthesize once, `release-lease`, remove `.active-task`, mark the task complete.

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
defer or reject with a reason, and prefer deleting mechanism to adding it. Without this, an
open-ended audit keeps finding ever-smaller issues: multiagent 1.5.0 took 12 rounds of findings.

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
  the headless team driver (below).
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

### Publishing a worker's text: `--out`

When a worker's returned text is the deliverable (a note, a summary, a review), `--out <path>`
has the engine write it instead of the conductor:

    python3 $E dispatch-worker --task … --role bulk_worker --brief … --out notes/paper-x.md

It refuses before launch when the suffix is code, the destination is reserved
(engine state of any task, any `.git*` component, `.claude`, `.codex`, `.vscode`, `.mcp.json`),
`authorize_action` denies the path, or `--write` is also given. On success the engine writes an
immutable snapshot, then the destination, then `outputs/<dispatch_id>.json` (account, backend,
model, attempt, sha256, lease generation). An unsuccessful worker result publishes nothing; its
`status: failed` record is written only if the lease check and state write succeed. Publication is not atomic: a guard failing partway (lost lease,
narrowed scope) can leave a snapshot or the destination written with no record; inspect both
before retrying. A result under 200 UTF-8 bytes after trimming leading and trailing whitespace is `below_min_bytes` and is not
published, because a one-word refusal also exits 0; pass `--min-bytes` for genuinely short output
(`0` disables it).

### Landing files: `--write` and `--exec`

`--write <path>` gives a worker one destination (a file, or an existing directory) inside
`target_repo` and `write_scope`:

    python3 $E dispatch-worker --task … --role implementer --brief … --write src/parser.py --exec

- Only a role with `may_write` (today `implementer`), only a Claude backend, never with `--out`.
  The worker runs `--restricted` with the file tools and a generated permission file whose `Edit`
  rules name the destination; any other path has no rule and is refused. Paths containing
  `*?[]{}!` are refused, not escaped. `--write` is refused while a managed-settings file exists.
  Nothing under `~/.claude` works as a destination (the CLI asks a human).
- Before launch the engine copies the destination to `writes/<dispatch_id>.before`;
  `restore-write --task-dir … --dispatch-id <id>` puts it back, and refuses if the destination
  changed after the run was recorded. `outputs/<dispatch_id>.json` carries the change set and one
  of `succeeded`, `succeeded_no_change`, `partial` (failed but changed files), `failed`, or
  `unknown` (not inspectable; never success). A rate-limited write is not retried. Where git is
  blind, as in a vault that ignores `40_Resources/`, the baseline is the complete record.
- `--exec` (requires `--write`) adds `Bash` with a named allowlist: `python3`, `python`, `ls`,
  `cat`, `head`, `tail`, `sed -n`, `grep`, `wc`, `find`, `git diff`, `git status`, `git log`.
  **Bash writes are not confined to the destination and are outside the change set**; the brief
  is their only containment, and the recorded `enforcement` says so. Use it for code with a test
  oracle: in three runs (2026-09-30) it matched a single session's catches at the same cost (within
  run-to-run spread) and half a no-Bash implementer's tokens and time.
- A brief for summary notes must carry the note's frontmatter schema; a worker infers none of it.

### Agent workers needing other tools: the headless team driver

`paper-reviewer` and `book-summarizer` render pages, crop figures and run the vault's gates, which
are outside `--exec`'s allowlist. The sanctioned route is one headless `claude -p --agent <name>`
process per chapter under `CLAUDE_CONFIG_DIR=~/.claude-team`, cwd the vault, launched by a driver
under a contract:

    python3 _shared/adapters/book_summarizer_team.py --task-dir tasks/<id> --job tasks/<id>/job.json

Start from `_templates/book-summarizer-team/`. The conductor supplies the PDF path, page offset and
per-chapter page ranges in the job. One chapter at a time; `--jobs N` only when the user authorized
parallel chapters. On rerun, a chapter is skipped when its `x.00` overview note exceeds 2,000
bytes and carries an `agent:` line. After each attempt the driver runs `record-attempt`, and after each built chapter
`record-author`, an assertion (see "Authorship"). The
shell grant is a named allowlist, but nothing intercepts a write: containment is the brief, and the
contract's `deviations` must say so. The model is the job's (default `claude-sonnet-5-5`).

### What a dispatched worker inherits

A Claude worker inherits nothing from your profile: `--restricted` drops user, project and local
settings, including plugins, hooks and the global `CLAUDE.md`. The engine appends a baseline (identity, American
spelling, state assumptions instead of asking, code conventions); vault layout, HPC or Zotero
details belong in the brief. The worker's cwd is `target_repo`. To read elsewhere, declare
`read_scope`: absolute existing directories, never `$HOME`, a filesystem root, or anything holding
credentials or agent configuration. The engine passes them to Claude as `--add-dir`, which also
grants write, so under `--write` every read root must equal or sit inside the destination.

## Authorship

A reviewer is cleared against what was **observed** producing the artifact, recorded in
`observed-author.json` beside the contract. `dispatch-worker` records a CLI producer
(`implementer`, `bulk_worker`) after exit 0, or under `--write` whenever the change set is
non-empty, failure included. The hook records a
native producer before the call runs, since PreToolUse cannot see the outcome; a session that did
not load the hook records nothing. The record accumulates every family that produced part of the
artifact.

`critic` and `verifier` are refused when the record is unreadable or names an unknown family; when
it names more than one family (mixed: split the artifact or review by hand); when it contradicts
`author_family` (fix the contract, not the sidecar); when a `--write` reservation recorded no
outcome; and when `roles_plan` includes a producer but nothing was observed. The conductor's family
stands in only for a contract that planned no producer.

`record-author --task-dir … --family … --source …` stores an **assertion**. It only adds families a
reviewer must differ from. It stands in for a missing observation only when the user has
recorded `authorship_assertion` under `approvals.user`; then assert every producing family. The
engine trusts `approvals.user` as the user's word; it authenticates nobody.

A `--out` publication also records its producer in `outputs/<dispatch_id>.json`; a `runner`
publication is attributed only there.

## Host adapters

- **Claude Code:** several spawns in one message run concurrently.
- **Codex:** no batch spawn; one `spawn_agent` at a time, at most three live
  (`max_active_children: 3`). `fork_turns` defaults to `all`, and a full-history fork accepts no
  `model` or `reasoning_effort`; when setting either, pass `fork_turns: "none"` or a positive
  integer string.
- Codex's child API reaches Codex models only. Fill a cross-family role with `dispatch-worker`,
  which runs the resolved backend's CLI with the brief on stdin, never with `spawn_agent`.

The engine counts CLI workers on the lease against `min(max_fanout, max_active_children)`, so a
real dispatch needs a live lease you own. It bounds accidental fan-out; it is not a
security boundary. A process killed mid-update leaves
`tasks/<id>/lease.lock`: every later lease operation times out naming it, and expiry does not clear
it. Stop the task's processes and delete it by hand.

The dry run reports `enforcement`:

| Host | Enforcement | Meaning |
|---|---|---|
| `codex` | `os-sandbox-read-only` | The OS refuses writes. |
| `claude-code` | `restricted-tool-surface` | `--tools Read,Grep,Glob` removes Bash and the write tools; `--strict-mcp-config` drops MCP servers. Binds the agent, not the process. `--write` and `--exec` extend it (`write.enforcement`; the write record). |

## Conductor host support

A host may conduct when all three hold, each machine-checked:

1. Its backend is a candidate of the `conductor` binding (`claude-frontier`, `codex-frontier`).
2. Its host has a `conductor_adapters` entry in `routing.yaml`.
3. That entry's `dispatch_hosts` reaches an independent `critic` and `verifier` for every author
   family. `dispatch_hosts` fails closed: omit it and the adapter dispatches nothing.

**Codex.** `engine/adapters/codex_pretool.py`, registered in `~/.codex/hooks.json`, applies the
Claude hook's rules to Codex: shell commands, every file an `apply_patch` touches, and
`spawn_agent` children (as family `codex`, so a Codex child cannot review Codex work). It fails
open: Codex runs the call if the hook is untrusted, crashes or times out, and editing the hook
entry's command, matcher or timeout voids its trust silently until it is re-trusted (the hash is
in `codex app-server`'s `hooks/list`). Call `python3 $E authorize --task … --action '<json>'` for
anything the hook does not see.

**Claude Code.** The hook sees tool calls only. It denies what looks like a worker CLI typed into
Bash, but that match is a heuristic; an absolute path or a variable defeats it.

A host with no conductor adapter takes no lease and operates advisory.

`agy` (Gemini) is not wired; revive it from commit `8e57af8`. With two families, one vendor's
outage leaves `critic` and `verifier` with no independent candidate.

## Two gaps

**What cannot land.** A Codex implementer returns a patch, and nothing applies one:
`apply-worker-patch` does not exist, and the hook denies `git apply` during an active task. Apply
it by hand with file tools, or use a Claude implementer with `--write`.

**What cannot execute.** Among CLI-dispatched Claude workers, only a `--write --exec` producer gets
Bash; a Claude critic or verifier reads tests but cannot run them. A Codex worker can run commands,
but its default read-only sandbox blocks every write, temp files included, so a suite that needs a
temporary directory errors (4 of 4 Stage 1 critic runs). Add `--review-copy` to a Codex critic or
verifier dispatch: the engine copies `target_repo` (without `.git`, caches and virtual environments;
refused above 20,000 files or 500 MB) into a fresh temporary folder, runs Codex there with
`--sandbox workspace-write` (`/tmp` excluded, `TMPDIR` inside that folder), and deletes it
afterwards; the original stays read-only
(measured 2026-09-30: 281 tests ran, OK). Codex-authored work's verifier is `claude-mid-team`
first, which cannot run tests; for executed evidence there, run the suite yourself or spawn a native
Claude verifier under the hook. Report executed verification only for checks that actually ran.

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

During an active task, write with file tools, not shell redirection or bulk shell commands. Without
a hook (Codex), call `authorize` before a write and keep every mutation inside `write_scope`.
