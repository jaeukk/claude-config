# Creating a tracker block: submitting sessions and launchers

Moved verbatim from the `sim-update` skill (2026-11-20), with the three placement rules below added.

- Start the fence at column one: `simtrack.py` does not see an indented fence.
- Quote any job ID that YAML would read as something else, above all a Slurm array-task ID:
  `"12345678_3"`. Unquoted, it loads as `123456783` and the block is quarantined.
- Inside `verify.outputs` and `verify.logs` the script reads only `paths` or `glob`, `count` and
  `reject_regex`; any other key nested there is ignored. Put a hash or any other check at the top
  level of `verify` (`additional`, `hashes`, `case_manifest`, …), where it becomes a judgment item.
- Declare a controller and its ledger in the block (`controller:` or `job_registry:`, or a job
  whose `role`, `file` or `name` says controller). The script cannot see one described only in
  the note's prose and would close the tracker on the parent's success.


When a workflow creates a tracker, its submitting session must also register the Active row with `simupdate.py register KEY`. Record the submission manifest, full job IDs, array expression, and specific verification paths/counts before handing off. Strictly parse the new block with duplicate-key rejection and require identity, host, scheduler, specific `verify`, and nonempty jobs (or `jobs: []`, `registration: incomplete`, and a submission-receipt path). Submission failure or interruption leaves an explicit incomplete registration to reconcile from submission receipts, never a fabricated job ID. For a staged campaign, the submitting session should validate that every downstream input is produced by the upstream manifest, using one shared case identity and output-path definition. Record the case-manifest path and expected case IDs per lane in `verify`. Isolate MPI and other child-process stdin from manifest streams. Record expected cases per lane and compare actual started/finished case IDs with that manifest: `all lanes finished` alone can hide omitted cases. A scheduler dependency guarantees an exit condition only, not file coverage or scientific acceptance. These checks do not authorize a polling session to submit jobs.

### Local wrapper receipt

The launch record must contain a `machine` map with hostname and machine ID plus job `id` (a never-reused run ID), `pid`, `boot_id`, `start_ticks`, and an absolute `receipt` path whose filename includes the run ID. The wrapper must await **every required child** and aggregate outcomes so any child's failure yields a nonzero `exit_code`, even if the last child succeeds. It commits a UTF-8 JSON receipt in the same directory via temporary file, flush and `fsync`, atomic no-replace commit (for example, `renameat2` with `RENAME_NOREPLACE`), then directory `fsync`. Required fields are `schema: 1`, `run_id`, `host` (`office-desktop` or `laptop`), `machine_id`, `boot_id`, integer `pid`, integer `start_ticks`, `started_at`, `finished_at` (timezone-aware timestamps), and integer `exit_code`. Extra application metadata is allowed. Accept it only if read at the recorded path, its identity fields **including machine ID** exactly match the launch record and the current execution machine's hostname and machine ID match the block; the receipt's boot ID must match the launch record, not the current boot. Timestamps must be coherent. An absent, partial, mismatched, or uncommitted receipt is `UNKNOWN`; its absence can never be repaired by an output file or log. The receipt records wrapper outcome; application checks still apply.

Example receipt and launch record (sim-tracker's `simtrack.py` checks exactly these fields; this does not migrate old blocks):

```json
{"schema": 1, "run_id": "square-020-r4000-20261021T170500", "host": "office-desktop", "machine_id": "<ID from /etc/machine-id>", "boot_id": "<UUID from /proc/sys/kernel/random/boot_id>", "pid": 12345, "start_ticks": 67890123, "started_at": "2026-10-21T17:05:00+09:00", "finished_at": "2026-10-21T18:00:00+09:00", "exit_code": 0}
```

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
  additional: "Require 61 finite spectrum rows and convergence of both drives."
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
