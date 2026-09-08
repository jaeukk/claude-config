# Open items — multiagent orchestration

Snapshot 2026-09-08, after the Orca adapter hardening (commits `406a662`, `e5598d4`). This is
the one place open work lives. Close an item by deleting it here; if it was a defect, note the
fix in `_shared/learnings.md` or `_shared/design-basis.md`. `docs/final-plan.md` § "Next
controlled revision" is superseded by this file.

## A. Gaps that shipped documented, not fixed

| # | Gap | Why it matters | Documented at | What closes it |
|---|---|---|---|---|
| A1 | A returned patch has no sanctioned landing path: `git apply` is denied by the hook, conductor edits cap at two code files. | Blocks Codex as default implementer; any CLI-dispatched implementer of either family hits it. | `docs/architecture.md` "Known gap" | `apply-worker-patch`: bind an engine-recorded implementer dispatch to a patch digest, require the live lease, validate every path (renames, symlink escapes) against `write_scope`, apply those bytes. Never by relaxing the hook or zeroing the counter. |
| A2 | A CLI-dispatched Claude verifier cannot execute (`--tools Read,Grep,Glob`, no Bash). Codex-authored work resolves to `claude-mid`, which can read tests but not run them. | Executable verification of Codex work needs a native Claude subagent; a Codex conductor has no such route and must hand off. | `skills/multiagent/SKILL.md` "What cannot execute" | A process-level sandbox for Claude workers (target repo mounted read-only, disposable copy for tests), or a verifier role that is allowed to write only under such a sandbox. |
| A3 | A Codex conductor has no enforcement: no PreToolUse equivalent. Contracts are validated, never intercepted. | Independence on Codex is a contract kept, not enforced. `spawn_agent` for a same-family critic is not stopped. | SKILL "Conductor host support" | A Codex-side hook adapter, if Codex ever exposes one. Until then: nothing; say "policy-validated". |
| A4 | `claude-frontier` pins `claude-opus-5`; sessions started outside `multiagent/` run Fable 5.1. `validate-task` does not check the running model. | Contract asserts a conductor model that is false for such sessions. **User decision 2026-09-07: ignore.** Recorded so it is not rediscovered as a bug. | D12 in `_shared/design-basis.md` | Conduct from `multiagent/` (local settings pin Opus 5) or `/model opus-5`. Or a check against the real session model, if the host ever exposes it. |
| A5 | Orca residual: a runtime-side mutation can complete after its client died; Orca gives no way to query a request whose id was never received. `abandoned` can then drop an attempt whose dispatch appears later. | The one path by which an adapter attempt loses its reservation with a worker alive. Bounded by the 600 s launch cap and the unchanged-baseline check, not eliminated. | `engine/adapters/orca_worker_start.py` docstring, SKILL | Orca feature: client-supplied idempotency key on `worker-start`, or a "pending mutations for task" query. Feature request, not local work. |
| A6 | Engine sidecar (`tasks/<contract>/observed-author.json`) and Orca run record (`tasks/orca/<run_id>/`) are separate authorship stores. Crossing transports needs a hand-written bridge. | Reviewing an Orca-produced artifact through the engine path required recording the author manually (done once, 2026-09-08). | SKILL "Authorship is a separate state store", learnings 2026-09-08 #4 | A mapping engine `task_id` ↔ Orca `run_id` that both dispatchers consult; or one store keyed by both. |
| A7 | `codex-mid` and `codex-fast` pin the same model (`gpt-5.6-terra`); the tier there is effort only, while on Claude it is a different model. | "Tier" means two things across families. | — (this file) | Split when a lower-tier Codex model exists. |
| A8 | The Orca adapter locks with `fcntl.flock`: Linux and WSL only. | Native-Windows `orca` cannot run it. | adapter docstring | `msvcrt.locking` branch or `portalocker`, when native Windows is actually a target. |

## B. Missing regression test for the Orca adapter

Seven review rounds hardened `engine/adapters/orca_worker_start.py`; every case was verified
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

- `docs/final-plan.md` § "Next controlled revision" still says the only deferred capability is
  direct Codex writing. Replace its body with a pointer to this file.
- `~/.multiagent` (the deployed global fallback) is stale since 2026-09-01. Deploy sources are
  clean as of `e5598d4`; run `scripts/deploy-multiagent.sh global`.
- Codex-side discovery of the renamed skill (`skills/multiagent/agents/openai.yaml`, prompt
  `$multiagent`) has not been verified from a Codex session since the rename.
- The audit-cycle loop (N rounds of critic → fix → critic) remains a practice, not a
  procedure: the skill mandates one critic pass and one verifier pass. Encode it only if the
  manual loop proves error-prone; two commands per cycle has not been.
