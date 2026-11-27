# What `simupdate.py` does

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
