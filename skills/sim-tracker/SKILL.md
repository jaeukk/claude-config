---
name: sim-tracker
description: Cheap status polling for PBS on Nurion, Slurm on Neuron or Della, and explicitly identified local desktop or laptop runs. Update sim-tracker blocks from actual observations only; never verify, close, or edit the simulation registry. Use for "check sim status", "update the tracker", or a scheduled status loop. Use sim-update for reconciliation and verification.
---

# sim-tracker

The project's research-note `sim-tracker` blocks are the job records. Locate the project's notes root from its `CLAUDE.md` or `AGENTS.md`; ask if none is recorded. Search **both** `10_ResearchNotes/01_NotConfirmed/` and `02_Confirmed/` for every block without `closure:`. Poll a block if it has an unverified job or an explicitly unresolved controller, successor, or ledger. A cached top-level `complete` or `failed` does not remove it from scope. Skip already verified terminal jobs; an entirely verified historical block awaiting closure needs sim-update, not another poll. If the user names a note, limit the scan to that note.

Parse the fenced YAML, never a prose table. `id` and `tracker_id` are aliases for the same tracker key; if both exist and differ, report a conflict and do not write. Preserve existing fields, job IDs, and readable historical timestamps; write new `last_polled` timestamps with a timezone offset. `scheduler:` chooses the poller; never infer it from `host:` or interpret a Slurm `Q` as PBS. An unsupported host/scheduler combination is an error, not a cue to rewrite the block.

| `host` | `scheduler` | Scheduler access | Workdir and verification file access |
| --- | --- | --- | --- |
| `nurion` | `pbs` | SSH `nurion` | SSH `nurion-dm` |
| `neuron` | `slurm` | SSH `neuron` | SSH `neuron-dm` |
| `della` | `slurm` | SSH `della` | SSH `della` (no `della-dm`) |
| `office-desktop` | `local` | Directly or through an explicitly mapped SSH endpoint on the identified office desktop | That execution machine |
| `laptop` | `local` | Directly or through an explicitly mapped SSH endpoint on the identified laptop | That execution machine |

Before a local poll, establish the **execution machine's** identity from the block's `machine.hostname` and durable `machine.machine_id` (for Linux/WSL, `/etc/machine-id`) and the project's endpoint mapping. Read process and files directly only when **both** hostname and machine ID match. Otherwise use SSH only through an explicitly mapped endpoint, verify both identifiers before reading its `/proc` or files, and keep the result tied to that remote execution machine. The current office desktop hostname `MZ-42-office` is informative, not a substitute for the machine ID; the laptop's identity must be recorded separately. `ssh localhost -p 2022` is a reverse tunnel to the laptop, **not** the current local machine; use it only if the project maps that endpoint to the laptop and its remote identity matches. `labws` is distinct. The local process contract below supports Linux/WSL; another OS needs a documented identity and receipt adapter. With no mapping or identity match, report `UNKNOWN` and do not poll or edit that local block.

## Cheap polling

- Group unverified jobs by endpoint and batch the scheduler query by their **full IDs**, including array IDs. Bound each query to the IDs in the selected blocks. Never run one SSH command per job or search an entire queue/accounting history.
- For Nurion only, search Gmail label **`HPC notices/Nurion`** by display name and exact PBS job ID first. Begin mail gives at least `R`; end/abort mail gives `F` and `Exit_status`. Deduplicate events by ID and time. If mail is unavailable or leaves jobs unresolved, socket-check `nurion`, then use one batched `qstat -x` for selected IDs; use `qstat -xf` where exit fields are needed. A missing mail or purged qstat record is `UNKNOWN`, never finished. No Gmail pass for Neuron or Della.
- For Slurm, socket-check the correct host, then use bounded `squeue -h -r -j <IDs>` and `sacct --array -P -n -j <IDs> -o JobID,State,ExitCode,DerivedExitCode` (or equivalent fields carrying array indices). If `--array` is unavailable, use the same bounded `sacct` query without it; grouped/pending rows are unresolved until every task has its own row. A `squeue` error is no state evidence; continue to `sacct` if available. Keep the native `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`, etc. on each job; store `ExitCode` as `exit` and `DerivedExitCode` as `derived_exit` when observed. Distinguish allocation/array task rows from `.batch`/`.extern` step rows. For a submitted array, record `array: <expression>` on its job row (or reference an authoritative submission manifest); expand its expected indices, ignoring concurrency suffixes such as `%5`, and compare with returned `JobID` values containing `_<index>`. `JobIDRaw` is an internal numeric ID and cannot identify an array index. Record `tasks_expected` and `tasks_missing` on the job row when coverage is known. Unknown expected coverage, missing tasks, or missing accounting fields leave the parent unresolved; never write a bare parent `COMPLETED` from partial coverage. Disappearance from `squeue` is not completion. Do not overwrite a verified terminal result with older or weaker evidence.
- For a local run, require a unique run ID, exact execution machine identity, and the wrapper's `pid`, Linux `boot_id` (`/proc/sys/kernel/random/boot_id`), and `start_ticks` (the 20th whitespace-separated token after the last `)` in `/proc/<pid>/stat`, which is field 22 in `proc_pid_stat(5)` numbering). `kill -0`, a PID alone, tmux session names, and log presence do not prove identity or completion. A live process counts as running only when the hostname, machine ID, and all three process identity fields match. A vanished or mismatched process without its valid final receipt is `UNKNOWN`. Read a final receipt only after it is atomically committed; a matching receipt supplies observed exit status. The receipt contract is below.

For remote SSH, follow the project's socket/auth rules: do not block on OTP or retry after authentication failure. Report an unavailable endpoint without inventing state. Use the file endpoint in the table for any optional, batched peek at terminal-job outputs; a peek is diagnostic only and cannot set `verified`.

## Local wrapper receipt contract

The launcher must use one wrapper process per run and give each run a unique, never-reused `run_id`. Record the execution machine's hostname and machine ID, wrapper PID, boot ID, start ticks, and an absolute `receipt` path at launch. The wrapper must wait for **every required child**, aggregate their outcomes, and set a nonzero exit if **any** required child fails; the last child's success cannot hide an earlier failure. It writes a UTF-8 JSON object with these required identity and outcome fields (extra application metadata is allowed):

```json
{"schema": 1, "run_id": "square-020-r4000-20261021T170500", "host": "office-desktop", "machine_id": "<ID from /etc/machine-id>", "boot_id": "<UUID from /proc/sys/kernel/random/boot_id>", "pid": 12345, "start_ticks": 67890123, "started_at": "2026-10-21T17:05:00+09:00", "finished_at": "2026-10-21T18:00:00+09:00", "exit_code": 0}
```

Write a temporary file in the receipt directory, flush and `fsync` it, atomically commit it to the final path without replacement (for example, `renameat2` with `RENAME_NOREPLACE`), then `fsync` the directory. The final filename must include the run ID so a later run in the same workdir cannot collide with an old receipt. The wrapper should report a failed receipt commit as a failure. The reader accepts a receipt only when it was read at the recorded path; its run ID, host, machine ID, boot ID, PID, and start ticks exactly match the **launch record**; the current execution machine's hostname and machine ID match the block; timestamps are coherent; and `exit_code` is an integer. A receipt from an earlier boot remains valid when its boot ID matches the launch record. A zero receipt exit still needs sim-update's application checks; no receipt means no proven success.

Example of a new local block (this does not migrate old blocks):

```sim-tracker
id: 2026-10-21-square-020-local-fdtd
host: office-desktop
scheduler: local
machine: {hostname: MZ-42-office, machine_id: "<ID from /etc/machine-id on the execution machine>"}
workdir: /absolute/path/to/square-phi0.20-res4000
status: running
last_polled: "2026-10-21T17:05:00+09:00"
verify:
  outputs: {glob: spectrum.csv, count: 1}
  logs: {glob: run.log, count: 1, reject_regex: "Traceback|Error|Aborted"}
  additional: Require 61 finite spectrum rows and convergence of both drives.
jobs:
  - id: square-020-r4000-20261021T170500
    file: run.sh
    state: RUNNING
    exit: "-"
    pid: 12345
    boot_id: "<launch boot UUID>"
    start_ticks: 67890123
    receipt: /absolute/path/to/square-phi0.20-res4000/square-020-r4000-20261021T170500.receipt.json
```

## Writeback

Update only states and exit fields supported by **fresh observations**. Use the real observation time with a timezone offset for `last_polled` only if at least one selected job was actually observed; it is a block-level time, not proof that every job was observed. With no observation, leave it unchanged. Set top-level `failed` for an observed nonzero/failed terminal outcome; otherwise keep `running` while any job is queued/running or unresolved. All observed zero exits are only *ready for sim-update*, not verified success or automatic closure; leave the top-level status as is pending verification. Preserve `verified` provenance and never regress a verified terminal job. Do not rewrite unchanged job lines.

Never edit a note in `02_Confirmed/` (an exported PDF may depend on it); report the proposed state instead. Never add `verified` or `closure`, move notes, or touch `SimulationRegistry.md`. Report concise per-endpoint results and any unobserved IDs. A scheduled invocation does not grant extra write or SSH authority.
