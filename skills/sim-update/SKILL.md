---
name: sim-update
description: Reconcile a project's simulation registry with research-note trackers, poll PBS, Slurm, or explicitly identified local runs, verify terminal jobs against scheduler and application evidence, and close only proven or owner-overridden trackers. One script does every mechanical step; the model handles only the judgment items it lists. Use for /sim-update or requests to reconcile, verify, or close simulations. Use sim-tracker for a cheap status-only poll.
---

# sim-update

Reconciliation, polling, the scheduler gate, declared outputs/logs checks, and all writes of
`verified`, `closure`, and `SimulationRegistry.md` are mechanical: one script does them. Your work is
the judgment queue it prints. Never edit `verified`, `closure`, tracker job states, or the registry by
hand; record every decision through the script, which re-runs the mechanical gates first.

```bash
S=~/.claude/skills/sim-tracker/scripts/simupdate.py
python3 $S [--project NAME | --root DIR | --all] [--dry-run] [--no-poll]       # reconcile, poll, verify, close
python3 $S accept KEY --checks "<what you checked and found>" [--mail FILE] [--archived] [--ledger-checked]
python3 $S override KEY --by OWNER --reason "<decision and unresolved evidence>"
python3 $S register KEY
```

## Procedure

1. **Scope.** With no flag the project comes from the current directory (research-note's
   `find_root.py`); pass `--project NAME` when the user named one; if it exits 1 with the root
   unresolved and nothing was named, use `--all`. If a project has no registry, ask the owner where it
   is; do not create one.
2. **Run it** and relay its report. Exit 3 means an SSH endpoint was unavailable: ask the user to open
   it (`ssh <host>`, OTP, leave it open), never retry SSH yourself, then rerun.
3. **Report lines and what to do with each:**
   - `VERIFIED_SUCCESS -> WRITTEN`, `registry row moved`: done. `-> PROPOSED ONLY`: the block is in
     `02_Confirmed/` (never edited, an exported PDF may depend on it); report the proposed closure and
     ask the owner for an amendment or re-export decision.
   - `WAITING`: nothing to do yet.
   - `FAILED`, `APP CHECK FAILED`: diagnose from the named jobs, checks, and evidence files, and give
     a concrete repair path (PBS `.e`/`.o` files are often empty when scripts redirect output; read the
     declared application logs, often `*_o.log` in the workdir). The tracker stays open unless the owner explicitly directs an override.
   - `UNVERIFIABLE`: the block has no usable `verify` contract. Report it; never invent acceptance
     criteria.
   - `EXCLUDED (...)`: registry drift. `ORPHAN_NOTE` is fixed with `register KEY` when a submission
     workflow or the user asks for registration. Other repairs only on an explicit request to fix
     records (see "Record repair").
   - `JUDGMENT NEEDED`: work through every item below, then run the `accept` command it prints.
4. **Judgment items** (read-only on the file endpoint: `nurion-dm`, `neuron-dm`, `della`):
   - **verify items needing judgment** (`additional`, `case_manifest`, `expected_case_counts`,
     `role_outputs`, hashes, and the like): perform every stated check; none is optional commentary.
     For multi-case lane workflows the contract must name a case manifest and expected case IDs or
     counts; match actual completed case identities to it. A present marker or output file alone
     does not prove valid content. If any check fails or cannot be performed, do not accept: report
     the tracker as failed or `UNVERIFIABLE` with the missing evidence.
   - **no fresh scheduler record** (history purged). Either search Gmail label **`HPC
     notices/Nurion`** (by display name) for each exact job ID; only end/abort mail with an
     `Exit_status` counts, a begin mail is not terminal. Write the results as a JSON list
     `[{"id": "<job ID exactly as written in the tracker>", "exit_status": <int>, "message": "<mail ID>",
     "received": "<date>"}]` and pass `--mail FILE` (PBS jobs only; the script saves it as evidence). Or rely on an archived capture: first check
     for later executions (exact-ID PBS begin/requeue mail after the archived end; Slurm
     `sacct --duplicates -j <ID>`; tracker, ledger, and submission manifests for reruns or later
     writers of the declared outputs) and establish that the outputs belong to the captured
     execution; then pass `--archived`. The script itself checks the capture's index entry, hash,
     command, endpoint timestamps, and terminal record. An empty mail search alone establishes nothing.
   - **controller/ledger declared** (`job_registry` or `controller` in the block, a job whose role,
     file or name says controller, or `verify.additional` naming a controller, successors or a
     ledger; a controller mentioned only in the note's prose is yours to notice): resolve the
     authoritative ledger against every submitted ID. Each required successor must be listed in a
     tracker and verified, or resolved by an owner override. A controller's own zero exit or a closed
     parent array does not imply successor completion. Then pass `--ledger-checked`.
   - `--checks` states, in one or two sentences, what you checked and what you found; it is recorded
     in the closure reason.
5. **Owner override** only on the owner's explicit decision, with a nonempty reason naming the
   unresolved evidence: `override KEY --by OWNER --reason "..."`.
6. A verification request does not authorize launching, repairing, or resubmitting simulations.

## Record repair

Only on an explicit request to fix records. The block is the source record; the registry is a derived
index. Allowed, conservative repairs: add a missing Active row (`register KEY`); correct an
unambiguous path or static field in a registry row from a valid source block; return an unsupported
(index-only) Closed entry to Active after inspecting the raw fence and establishing that it has no
`closure` field. Preserve the previous index claim and the repair reason in a dated repair record
under `<root>/20_Progress/`. Ambiguous identity, conflicting source records, and malformed historical
blocks stay quarantined: never repair malformed YAML silently or use regex-extracted job states as
evidence. Confirmed notes stay unchanged; record corrections in a dated amendment with the exact
source location and hash. Do not create a second tracker with the same ID. Inside a tracker fence,
references to other notes are bare dates (`2026-03-20` or `2026-03-20:multi-stealthy`), not wikilinks.

## For submitting sessions and launchers

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

## What the script does

The contract `simupdate.py` implements (it reuses `simtrack.py` for parsing, polling, evidence, and
note writes); change the script, then this list. `--selftest` runs an offline end-to-end check.

- **Reconcile.** Every fence in both research-note folders, strictly parsed (simtrack); schema errors
  are quarantined and inventoried by note, fence line, and SHA-256 (saved as a `reconcile` capture).
  Registry rows are matched to blocks by tracker key; wikilinks are resolved from the vault root;
  table cells are split outside `[[...]]`. Drift codes `ORPHAN_NOTE`, `PATH_MISMATCH`,
  `STATIC_MISMATCH`, `DUPLICATE_TRACKER_ID`, `INDEX_ONLY_CLOSURE` exclude a tracker from verification,
  closure, and registry writes; `ORPHAN_REGISTRY`, `PROVISIONAL_SCHEMA_ERROR`, and `UNVALIDATED_CLOSED`
  are reported. An Active row is required for each valid, unclosed block with an unverified job, no
  jobs, or a controller/ledger; a fully verified unclosed block needs none.
- **Poll** with `simtrack.py` (same writeback rules), keeping each job's fresh observation.
- **Scheduler gate** from this run's observation only: PBS `F` with exit 0; Slurm `COMPLETED` with
  `0:0`/`0:0` and complete array coverage; local `EXITED` 0 from a valid receipt. A purged record is
  a judgment item unless the cached outcome is already a failure, which reports `FAILED`.
- **Application gate** for `verify.outputs`/`verify.logs` (`paths` or `glob` under `workdir`, exact
  integer `count`, `reject_regex` nested or at `verify` level): one read-only Python check per file
  endpoint (Python 3.6 compatible; the spec travels base64-encoded, never through a shell). Every
  declared path must exist, the match count must equal `count` (an integer >= 1; an empty match
  never passes), and no required log may match its reject pattern. Relative paths need an absolute
  `workdir`. A malformed check makes the tracker `UNVERIFIABLE` and cannot be waived by `accept`.
  Any other `verify` key is a judgment item.
- **Verification and closure** are written together, only when every unverified job passes, the
  application gate passes (it is re-run even when every job was verified earlier), and no judgment
  item remains (or `accept` acknowledged them). End mail counts only for PBS jobs whose ID matches
  exactly. An archived capture counts only if indexed with its stdout hash, taken on this tracker's
  scheduler endpoint with the right query, carrying endpoint timestamps, the job in its ID list, and
  an acceptable exit status (qstat 0 or 153, sacct 0), and if its latest record of the job is
  terminal success. Jobs get
  `verified: "<source>+outputs+logs[+checks]@<time>"` (source `qstat-xf`, `sacct`, `receipt`,
  `gmail:<capture>`, or `archived:<capture>`); the block gets
  `closure: {mode: verified_success, by: simupdate.py, at, reason}`. Writes go only to
  `01_NotConfirmed/` blocks, are re-parsed strictly before saving, and are atomic.
- **Registry.** After a block records a closure, its Active row moves to the Closed table in that
  table's column layout (closure mode and reason where the table has such columns); a tracker closed
  in this run with no row at all gets a Closed row; the move is retried on the next run if
  interrupted. `accept` and `override` exit 1 when they recorded nothing. `register` appends an Active row. Every registry write is
  atomic and only if the file did not change during the run.
