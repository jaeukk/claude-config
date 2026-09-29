---
name: multiagent
description: Conductor mode for Claude Code or Codex. The default is a single session; this skill adds the multiagent engine (task contracts, leases, independent review within an audit budget) only for cross-vendor review of consequential or untestable work, team-account routing, and contained writes or publication with a record. Use when invoked as /multiagent (formerly /orchestration, a name that now belongs to Orca's skill), when the user asks for conductor or orchestration mode, or when Claude and Codex should collaborate without recursively spawning conductors.
---

# Multiagent

## Scope: a single session first

The default is **one producer and no review loop**: this session, a native subagent (Claude
`Agent`, Codex `spawn_agent`; no contract), or one team-account worker with Bash
(`dispatch-worker --write <dest> --exec`). On two code tasks with a test oracle (2026-09-29,
four runs per arm), the critic → fix loop changed the score in 0 of 4 runs, and the multiagent arm
cost 2–3.7× a single session for scores within noise; most of that was the implementer lacking
Bash.

Reach for the engine only for:

- **(a) cross-vendor review** of consequential work, or of work with no test oracle
  (`audit_cycles` ≥ 1);
- **(b) team-account routing**: `dispatch-worker` is how a producer bills the team login with a
  record;
- **(c) contained writes or publication with a record**: `--write` (baseline, change set, restore)
  and `--out`.

On Claude Code a PreToolUse hook enforces the contract, but only in sessions launched from
`multiagent/`. Elsewhere, and on Codex, the contract is validated and kept, not enforced: say
**policy-validated**.

## Load the local authority

Policy is `multiagent/policy/{roles,bindings,backends,routing,approvals}.yaml` (JSON syntax). The
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
   task. Options cannot be abbreviated, and lease and event commands refuse a folder with no
   `task.yaml`.
4. For the hook to enforce the task, put its ID in `tasks/.active-task`.
5. `python3 $E dispatch-worker --task tasks/<id>/task.yaml --role <role> --brief <file>`, plus
   `--write` or `--out` (below). `--dry-run` prints the resolved command, account and
   `enforcement` without launching. Before a native spawn under an active contract, set
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

## Tiers and bindings

Backends are named `<family>-<tier>`. A tier is a routing role, not an effort level, and tier
order is a preference, not a measured ranking.

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
- `runner`: `codex-fast` first (equal accuracy at 26× fewer tokens on the recorded benchmark,
  `_shared/capability-profile.md`), then `claude-fast-team`, then `claude-fast`.

"First, then" is preference, not failover: `resolve_binding` returns the first compatible
candidate without a health check, and a failed worker is reported, not retried on the next one.
During a Codex outage, pass `--required-family claude`.

`dispatch-worker` transmits effort on every CLI path (`-c model_reasoning_effort` for Codex,
`--effort` for Claude). A native Claude spawn takes model and effort from its agent frontmatter,
not from the binding; the hook checks only that a compatible binding exists. Keep the frontmatter
in sync, and never report the resolved backend as the model that ran unless the frontmatter says
so. Model evidence: `references/model-refresh-2026-09-27.md`.

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

### Publishing a worker's text: `--out`

When a worker's returned text is the deliverable (a note, a summary, a review), `--out <path>`
has the engine write it instead of the conductor:

    python3 $E dispatch-worker --task … --role bulk_worker --brief … --out notes/paper-x.md

It refuses before launch when the suffix is code, the destination is reserved
(engine state of any task, any `.git*` component, `.claude`, `.codex`, `.vscode`, `.mcp.json`),
`authorize_action` denies the path, or `--write` is also given. On success the engine writes an
immutable snapshot, then the destination, then `outputs/<dispatch_id>.json` (account, backend,
model, attempt, sha256, lease generation). A failure records `status: failed` with a reason and
writes nothing. A result under 200 bytes of non-whitespace is `below_min_bytes` and is not
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
  Nothing under `~/.claude` works as a destination (the CLI asks a human for those paths).
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
  oracle: on 2026-09-29 the same argv matched a no-Bash implementer's catches within noise at
  about half the tokens and time.
- A brief for summary notes must carry the note's frontmatter schema (`citekey`, `zotero_key`,
  `tags`, …); a worker infers none of it.

### Agent workers needing other tools: the headless team driver

`paper-reviewer` and `book-summarizer` render pages, crop figures and run the vault's gates, which
are outside `--exec`'s allowlist. The sanctioned route is one headless `claude -p --agent <name>`
process per chapter under `CLAUDE_CONFIG_DIR=~/.claude-team`, cwd the vault, launched by a driver
under a contract:

    python3 _shared/adapters/book_summarizer_team.py --task-dir tasks/<id> --job tasks/<id>/job.json

Start from `_templates/book-summarizer-team/`. The conductor supplies the PDF path, page offset and
per-chapter page ranges in the job. One chapter at a time; `--jobs N` only when the user authorized
parallel chapters. A chapter whose `x.00` overview note carries an `agent:` line is skipped on
rerun. After each chapter the driver runs `record-author`, an assertion (see "Authorship"). The
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
real dispatch needs a live lease you own. That bounds accidental fan-out by a cooperating
conductor; it is not a security boundary. A process killed mid-update leaves
`tasks/<id>/lease.lock`: every later lease operation times out naming it, and expiry does not clear
it. Stop the task's processes and delete it by hand.

The dry run reports `enforcement`:

| Host | Enforcement | Meaning |
|---|---|---|
| `codex` | `os-sandbox-read-only` | The OS refuses writes. |
| `claude-code` | `restricted-tool-surface` | `--tools Read,Grep,Glob` removes Bash and the write tools; `--strict-mcp-config` drops MCP servers. Binds the agent, not the process. `--write` and `--exec` extend the string with what they grant. |

Never claim a Claude-hosted worker is sandboxed.

## Conductor host support

A host may conduct when all three hold, each machine-checked:

1. Its backend is a candidate of the `conductor` binding (`claude-frontier`, `codex-frontier`).
2. Its host has a `conductor_adapters` entry in `routing.yaml`.
3. That entry's `dispatch_hosts` reaches an independent `critic` and `verifier` for every author
   family. `dispatch_hosts` fails closed: omit it and the adapter dispatches nothing.

**Codex.** Codex 0.159 has PreToolUse hooks, but no multiagent adapter is wired to them yet. Until
one is, nothing stops a Codex conductor from filling `critic` with `spawn_agent`: call
`python3 $E authorize --task … --action '<json>'` before acting and `dispatch-worker` for every
cross-family worker, and describe the result as a contract kept, not enforced.

**Claude Code.** The hook sees tool calls only. It denies what looks like a worker CLI typed into
Bash, but that match is a heuristic; an absolute path or a variable defeats it.

A host with no conductor adapter takes no lease and operates advisory.

`agy` (Gemini) is not wired; revive it from commit `8e57af8`. With two families, one vendor's
outage leaves `critic` and `verifier` with no independent candidate.

## Two gaps

**What cannot land.** A Codex implementer returns a patch, and nothing applies one:
`apply-worker-patch` does not exist, and the hook denies `git apply` during an active task. Apply
it by hand with file tools, or use a Claude implementer with `--write`.

**What cannot execute.** Only a `--write --exec` producer runs commands. A CLI-dispatched critic or
verifier has no Bash, and the Codex read-only sandbox blocks temp-file writes, so a Codex critic or
verifier usually cannot run a test suite and reviews statically. Codex-authored work's verifier is
`claude-mid-team` first, which reads tests but cannot run them. For executed evidence, run the
suite yourself or, for Codex-authored work, spawn a native Claude verifier under the hook. Never
report a read-only review as executed verification.

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
Without a policy installation, apply these rules as advice and take the more restrictive action.
