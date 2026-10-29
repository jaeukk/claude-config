---
name: sim-tracker
description: Cheap status polling for PBS on Nurion, Slurm on Neuron or Della, and explicitly identified local desktop or laptop runs. One script does the whole poll; the model only runs it and relays the report. Never verifies, closes, or edits the simulation registry. Use for "check sim status", "update the tracker", or a scheduled status loop. Use sim-update for reconciliation and verification.
---

# sim-tracker

Polling is mechanical. Run the script; do not poll, parse, or edit tracker blocks by hand.

```bash
python3 ~/.claude/skills/sim-tracker/scripts/simtrack.py [--project NAME | --root DIR | --all] [--note YYYY-MM-DD] [--dry-run]
```

1. **Pick the scope.** With no flag, the script resolves the project from the current directory through
   research-note's `find_root.py`. If the user named a project, pass `--project NAME`; a note, `--note
   YYYY-MM-DD`. If it exits 1 (root unresolved) and the user named nothing, rerun with `--all`.
2. **Relay the report** (one line per endpoint query and per tracker). Add nothing it did not say.
3. **Exit 3** means an SSH endpoint was unavailable or wedged. Ask the user to open it (`ssh <host>`,
   enter the OTP, leave it open). Never retry SSH yourself: repeated failures risk account lockout.
4. **Hand-offs.** Lines ending in `sim-update` (all jobs terminal, failed, NO_JOBS, every job
   verified, excluded by the quarantine screen) are sim-update's job. Polling never verifies or closes.
5. A status request does not authorize launching, repairing, or resubmitting simulations.

## What the script does

This is the contract it implements; change the script, then this list, never one alone.
`--selftest` runs an offline end-to-end check.

- **Scope.** Every fenced `sim-tracker` block in both `10_ResearchNotes/01_NotConfirmed/` and
  `02_Confirmed/`. A block is polled when it has no `closure:` and at least one job without `verified`;
  cached top-level `complete`/`failed` does not exempt it. Verified jobs are never queried or rewritten.
- **Strict parsing.** YAML 1.1 with duplicate-key rejection. Missing or conflicting `id`/`tracker_id`,
  missing `host`/`scheduler`, an unsupported pair, malformed `jobs`, or a job ID whose YAML 1.1 value
  differs from its spelling (unquoted `12345678_3` loads as `123456783`) quarantines the block. Quarantined
  fences are screened as raw text (token boundaries, array/server suffixes allowed) for each selected
  tracker's key, full and numeric job IDs, and ledger paths; a hit excludes that tracker. Fence
  hashes, terms, and errors are saved as a `screen` capture.
- **Endpoints.** `scheduler:` picks the poller and is never inferred from `host:`.

  | `host` | `scheduler` | Query |
  | --- | --- | --- |
  | `nurion` | `pbs` | SSH `nurion`: one `qstat -xf <IDs>` |
  | `neuron`, `della` | `slurm` | SSH `neuron`/`della`: `squeue -h -r -j <IDs>` and `sacct --array -P -n -j <IDs>` (retried without `--array` if unsupported) |
  | `office-desktop`, `laptop` | `local` | `/proc` and receipt checks, only when this machine's hostname **and** `/etc/machine-id` match `machine:` |

  Each SSH endpoint is first checked with `ssh -O check`, then queried once through the live master
  with `BatchMode=yes`, `ControlMaster=no`, and every client authentication method disabled, so a
  master that dies mid-poll gives exit 255, never a login attempt. Each query is wrapped in `timeout`,
  with endpoint `date -Is` stamps around it. Job IDs that are not plain scheduler IDs (PBS `N` or
  `N.server`, Slurm `N` or quoted `N_M`) are rejected before reaching the remote shell. A local block
  for another machine is reported, not polled.
- **PBS.** `job_state` and `Exit_status` per ID; `exit` is set only for `F` with a numeric status.
  `Unknown Job Id` gives `NOT_FOUND`: cached `state`/`exit` are kept and the miss is recorded in
  `last_query`. No Gmail lookup: PBS mail for purged history is sim-update's fallback.
- **Slurm.** Native states. Allocation rows only (`.batch`/`.extern` steps ignored). For an array job,
  expected indices come from the job row's `array:` (throttle `%N` ignored); `tasks_expected`,
  `tasks_missing` (expected indices without their own sacct row), per-category counts, and
  `task_groups` are written. The parent gets a terminal state only when every expected index has its
  own sacct row with both exit fields present (missing fields leave a task unresolved), and parent
  exits `0:0` only when every task is `COMPLETED` with `0:0`/`0:0`; exits are written only for
  terminal states. A `squeue` error is no evidence;
  absence means `NOT_FOUND` only when `sacct` exited 0.
- **Local.** `RUNNING` only when boot ID and start ticks (field 22 of `/proc/<pid>/stat`) match the
  launch record. Otherwise the final receipt must exist at its recorded path, name the run ID, match
  every launch identity field, carry timezone-aware ordered timestamps, and have an integer
  `exit_code`; it then gives `EXITED` with that exit. Anything less gives `UNKNOWN` with state kept.
- **Evidence.** Raw stdout/stderr and a metadata JSON (exact command, IDs, exit status, local and
  endpoint timestamps, SHA-256 of raw bytes) go in `<root>/20_Progress/sim-evidence/<date>/` without
  ever overwriting an earlier capture, and each capture is appended to
  `<root>/20_Progress/SimulationEvidence_<date>.md` as soon as it is saved.
- **Writeback.** Only blocks in `01_NotConfirmed/`; `02_Confirmed/` blocks get a proposed change in the
  report. Each observed job gets `last_query: {at, source, result, evidence}`; the block gets
  `last_polled` (real time with offset, only after an actual observation) and `last_poll_evidence`
  (paths and hashes). `status` becomes `failed` on a freshly observed failure (and stays `failed`), or
  `running` while any unverified job is non-terminal; otherwise it is left for sim-update. Only changed
  job items are re-emitted, in their original flow/block and quoting style; new top-level values are
  written as JSON, which YAML never coerces. The edited fence is re-parsed strictly and must equal the
  intended data exactly, or nothing is written. The note is rewritten atomically, and only if it did
  not change during the poll. Never adds `verified`/`closure`, moves notes, or touches
  `SimulationRegistry.md`.
