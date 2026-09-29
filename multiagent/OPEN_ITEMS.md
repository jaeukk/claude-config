# Open items — multiagent orchestration

Snapshot 2026-09-30, after the Stage 2–3 cuts (`tasks/2026-09-29-cut-stage2-3/plan.md`, design-basis
D17). This is the one place open work lives. Close an item by deleting it here; if it was a
defect, note the fix in `_shared/learnings.md` or `_shared/design-basis.md`. The archived
`_archive/docs/final-plan.md` is superseded by this file.

## A. Gaps that shipped documented, not fixed

| # | Gap | Why it matters | Documented at | What closes it |
|---|---|---|---|---|
| A1 | **Narrowed 2026-09-17.** `--write` lands files directly from a worker into one declared destination, so the patch route is no longer the only way code can arrive. What remains unbuilt is applying a *patch* a worker returned (the Codex case: `codex-core` is read-only and can only hand back a diff). | A Codex implementer still cannot land its work; a Claude implementer now can. | SKILL "Two gaps" | `apply-worker-patch`, unchanged in design: bind a recorded dispatch to a patch digest, require the live lease, validate every path against `write_scope`, apply those bytes. |
| A2 | **Narrowed 2026-09-30.** A `--write --exec` producer can run commands; a CLI-dispatched critic or verifier still cannot. A Claude one has no Bash, and Codex's read-only sandbox blocks temp-file writes, so a Codex critic or verifier usually cannot run a suite (4 of 4 Stage 1 critic runs). Codex-authored work resolves to `claude-mid-team`, which reads tests but cannot run them. | Executed verification needs the conductor, or a native Claude verifier for Codex-authored work; a Codex conductor has no native Claude route. | SKILL "Two gaps" | A disposable writable copy of the target for the Codex critic/verifier (not built), or a process-level sandbox for Claude reviewers. |
| A3 | Codex 0.159 has PreToolUse hooks (`codex features list`: hooks stable), but no multiagent adapter is wired to them; the adapter is Stage 4. Contracts on Codex are validated, not intercepted. | Independence on Codex is a contract kept, not enforced. `spawn_agent` for a same-family critic is not stopped. | SKILL "Conductor host support" | A Codex hook adapter mirroring `claude_pretool.py` (Stage 4), with a tested deny. Until then, say "policy-validated". |
| A4 | `claude-frontier` pins `claude-opus-5-5`; sessions started outside `multiagent/` may use a different model. `validate-task` does not check the running model. | Contract asserts a conductor model that is false for such sessions. **User decision 2026-09-07: ignore.** Recorded so it is not rediscovered as a bug. | D12 in `_shared/design-basis.md` | Conduct from `multiagent/` (local settings pin Opus 5.5) or `/model claude-opus-5-5`. Or a check against the real session model, if the host ever exposes it. |
| A7 | `codex-mid` and `codex-fast` pin the same model (`gpt-5.6-terra`); the tier there is effort only, while on Claude it is a different model. | "Tier" means two things across families. | — (this file) | Split when a lower-tier Codex model exists. |
| A9 | Team-account routing covers CLI dispatch only: native subagents always run under the session login (`exclude_account_bound`). (`--host wsl` is gone and the Orca adapter archived, 2026-09-30.) | Team quota is spent only by `dispatch-worker`, `claude-worker` and the headless team driver. | `claude_pretool.py`, SKILL "Accounts" | A per-subagent account selector in Claude Code, if one ever exists. |
| A10 | The rate-limit classifier keys on `api_error_status == 429` plus text patterns; no captured fixture of a real team rate-limit envelope exists yet. **Captured 2026-09-23 (Codex, not team):** an exhausted Codex weekly window prints `ERROR: You’ve hit your usage limit. … try again at Sep 28th, 2026 6:24 PM.` to stderr with exit 1 and no 429, and `classify` returned `error` — see `tasks/2026-09-23-opus-55-benchmark/workers/critic/dispatch-1.log`. | A misclassified failure either skips the private retry or retries a non-limit error once. | `engine/accounts.py` `classify` | Capture one real 429 envelope per CLI version into `engine/tests` fixtures. |
| A11 | `authorize_action` keys on role and path only; it does not consult the selected backend's `writes_mediated` / `write_mode`, and `validate_policy` only warns for a read-only backend. The registered Codex backends are safe because their dispatcher is hard-coded `--sandbox read-only`. | A future backend advertising mediated writes would pass validation without that protection; today the conductor, not the engine, keeps this true. | — (moved out of SKILL "Approval and enforcement", 2026-09-30) | Check the resolved backend's write mediation in `authorize_action` before any worker write path. |
| A12 | The reviewer gate reads `roles_plan` as it stands. A native producer never named in `roles_plan`, or removed from it afterwards, leaves the plan producer-free, so a reviewer falls back to the conductor's family. | A conductor can reach a same-family review of a native producer's work by editing the plan. | — (moved out of SKILL "Authorship", 2026-09-30) | Record planned producers in the sidecar at first spawn, and read that history instead of the live plan. |

## A-write. Known limitations of `--write`, recorded rather than fixed

Named by the 2026-09-18 audit (three rounds, `codex-ceiling`) as limitations rather than blockers.
Reviews in `tasks/2026-09-18-post-audit/workers/critic/`.

- **`--assume-stopped` is a human judgment.** The reservation carries no worker PID and the
  worker runs under `subprocess.run`, so the engine has no evidence a particular worker or an
  orphaned child has stopped. Lease expiry does not establish it. Closing this means real process
  identity tracking, which is a bigger thing than this patch.
- **An interrupted *directory* replacement recovers by hand.** The workspace holding the
  post-dispatch contents and the staged baseline is preserved and named in the refusal; putting it
  back is manual.
- **Nothing under `~/.claude` can be a `--write` destination.** The CLI gates those paths as
  sensitive and asks a human, so a worker cannot edit this installation. Protective, but it means
  the engine cannot be maintained through its own write path.
- **`--exec` widens what a worker can write beyond the change set.** Bash can write outside the
  destination, and those writes are neither refused nor recorded; the brief is the only
  containment. This is the same trade the headless team driver already makes.
- **TODO: a session-start preflight** that proves, through the real hook path, that the hook is
  loaded before any native production starts. An unloaded hook records no native producer, which
  later blocks or misdirects reviewer independence, and static inspection of settings cannot
  prove it loaded.

## A-clone. TODO: no direct writes into `~/.claude` or `~/.agents`; work in a clone, merge by command

Decided 2026-09-18 as a todo. Since 2026-09-30 the clone workflow is the documented rule
(`AGENTS.md`); the enforcement layer and `scripts/merge-config.sh` below are still unbuilt. Today a dispatched worker cannot write under
`~/.claude` (the CLI's sensitive-path gate), but a conductor or native subagent can, under the
session's own permissions -- which is how every change this week reached the live config.

- **Enforcement layer.** A user-level permission deny that every session and subagent inherits,
  scoped to the *tracked* config trees only (`agents/`, `commands/`, `hooks/`, `multiagent/`,
  `scripts/`, `shell/`, `skills/`, `CLAUDE.md`, `README.md`, `settings*.json`) plus `~/.agents/**`
  and `~/.codex/**`. Never `projects/` (memory), `cache/`, `backups/`, `.credentials.json`: deny
  outranks allow, so a blanket rule cannot be carved back out. The user applies the rule; the
  engine mirrors it in `authorize_action` for sessions that load the hook.
- **Workflow.** A second checkout of `claude-config` is where conductors and workers edit, test
  and commit -- `multiagent/engine/` included, which retires the "engine cannot maintain itself"
  limitation. `scripts/merge-config.sh` fetches the branch, `git merge --ff-only` into `~/.claude`,
  redeploys `~/.multiagent`. Bash, not Edit, by design: the only way into the live tree is a
  fast-forward of something already committed and tested elsewhere.
- **Costs.** Two checkouts to keep in sync; a hotfix made by hand in `~/.claude` must be committed
  before the next merge or `--ff-only` refuses (correct, and a new habit); small doc fixes become
  clone → commit → merge → deploy.
- **Migration.** Move `temp/2026-09-18-worker-write` to the clone and continue there.

## A-cut. Left uncovered by the 2026-09-30 cuts

- **Nothing forces review of conductor code edits.** The `direct_code_files` cap is gone;
  `audit_cycles` is the only review lever, and it is opt-in.
- **`--exec` Bash writes are outside the change set** (see A-write).
- **The Codex critic still cannot execute suites that need temp files** (A2).

## Archived with the Orca adapter

The adapter moved to `_archive/engine/orca_worker_start.py` on 2026-09-30 (one development run,
no tests, its own authorship store). A5, A6 and A8 no longer apply to anything live. Reviving Orca
transport means restoring the adapter and writing item B's tests first.

| # | Gap | Why it matters | Documented at | What closes it |
|---|---|---|---|---|
| A5 | Orca residual: a runtime-side mutation can complete after its client died; Orca gives no way to query a request whose id was never received. `abandoned` can then drop an attempt whose dispatch appears later. | The one path by which an adapter attempt loses its reservation with a worker alive. Bounded by the 600 s launch cap and the unchanged-baseline check, not eliminated. | `engine/adapters/orca_worker_start.py` docstring, SKILL | Orca feature: client-supplied idempotency key on `worker-start`, or a "pending mutations for task" query. Feature request, not local work. |
| A6 | Engine sidecar (`tasks/<contract>/observed-author.json`) and Orca run record (`tasks/orca/<run_id>/`) are separate authorship stores. Crossing transports needs a hand-written bridge. | Reviewing an Orca-produced artifact through the engine path required recording the author manually (done once, 2026-09-08). | SKILL "Authorship is a separate state store", learnings 2026-09-08 #4 | A mapping engine `task_id` ↔ Orca `run_id` that both dispatchers consult; or one store keyed by both. |
| A8 | The Orca adapter locks with `fcntl.flock`: Linux and WSL only. | Native-Windows `orca` cannot run it. | adapter docstring | `msvcrt.locking` branch or `portalocker`, when native Windows is actually a target. |

### B. Missing regression test for the Orca adapter

Seven review rounds hardened the adapter (now `_archive/engine/orca_worker_start.py`); every case was verified
in-process during the session and none of it lives in the repo. Add
`engine/tests/test_orca_worker_start.py`, standard library only, mocking `subprocess.run`,
`invoke_start`, `run_id_for_task`, `conductor_host`, `current_dispatch`, `dispatch_status`, and
`run_lock` as needed, with a temp `--root` holding a copy of `policy/`. The cases to cover, in
the groups they were found in:

- **Receipt trichotomy** (`receipt_metadata`): accepted; Orca refusal for a bad flag and for a
  non-ready task (both `ok:false` + `error.code`) → rejected; `ok:false` with no error object,
  error as string, error without code, `ok:true` without `dispatchId`, bad `dispatchId`,
  garbage, top-level array → lost (held). Nested decoy `dispatchId` must not win.
- **`current_dispatch` strictness**: `dispatch: null` → None; dict with SAFE_ID `id` → id;
  `ok:true` without result, result without `dispatch` key, dispatch `[]`, dispatch without id,
  refusal, garbage, top-level `[]`/`null`/`42`/`"str"` → `StateError`; a hanging CLI →
  `StateError` within the query timeout; an unreachable CLI raises, never returns None.
- **Settlement predicate**, for both unrecorded states (`starting`, `outcome_unknown`) ×
  {review: `reviewed`; producer: `succeeded`, `retained-output`, `no-output`}: the baseline
  dispatch is refused ("already existed"); a dispatch that is not Orca's current one is refused
  ("not the task's current dispatch") — including the two-generation stale id; the current
  dispatch ≠ baseline reconciles with the right family; current `None` with an id offered is
  refused. `started` attempts: own dispatch settles, any other is refused.
- **Abandon**: old + unchanged baseline → released, settled author kept (family survives); young
  → refused; a new dispatch appeared → refused; a `started` attempt → refused; without
  `--confirm-no-output` → refused; first-ever producer with no settled author → record removed.
- **Reservation and launch**: the baseline snapshot is taken with the lock held;
  `prior_dispatch_id` and `reserved_at` are recorded; a rejected fresh launch releases the
  reservation; a rejected retry (`--resume-start`) keeps the original attempt; a launch
  exceeding the cap → `outcome_unknown`, reservation held; mutual exclusion refuses a second
  tracked launch; `--dry-run` refuses while an attempt is active and previews when the run is
  free.
- **Locks**: a second locker blocks while the holder lives; acquires immediately after the
  holder is SIGKILLed with no stale file; `settle()` performs both Orca reads with the lock
  held; abandon performs only the current-dispatch read.
- **Record validation**: `reserved_at` required numeric; `prior_dispatch_id` string or null; a
  stray `previous` key ignored; unsupported schema version, corrupt JSON, unknown family →
  `UnreadableAuthorRecord`.
- **CLI surface**: `--role conductor` refused; reserved passthrough (`--model`, `--terminal`)
  refused; `--run` mismatch refused; unknown task refused; `--conductor-host` contradicting
  attestation refused; exactly one of `--role`/`--settle`/`--resume-start`; settle without
  `--dispatch-id` refused; `--settle` with `--dry-run` writes nothing.

## C. Housekeeping

- `~/.multiagent` (the deployed global copy) was last deployed at `ab608a0` (2026-09-27). Run
  `scripts/deploy-multiagent.sh global` once this cut is merged into `~/.claude`. Whether anything
  on the Windows side still reads that copy is unverified; retiring `global` mode is a later
  decision.
- The System A files listed in `AGENTS.md` are legacy, kept pending their own retirement decision.
