---
name: sim-update
description: Reconcile a project's HPC simulation registry (20_Progress/SimulationRegistry.md) against the sim-tracker blocks in its research notes, poll the scheduler (PBS on Nurion, SLURM on Neuron), verify finished jobs fail-closed (exit status plus output/log checks), write results back, and close only trackers that are provably done. Use when the user invokes /sim-update or asks to reconcile, verify, or close simulation trackers. For a cheap status-only poll, use sim-tracker instead.
---

**Locate first.** `<root>` is the project's Obsidian notes root, recorded in the project's
`CLAUDE.md` (or `AGENTS.md`); the global notes convention puts research notes in
`<root>/10_ResearchNotes/{01_NotConfirmed,02_Confirmed}/` and the registry at
`<root>/20_Progress/SimulationRegistry.md`. All relative paths below are under `<root>`. If the
project records no `<root>`, or has no registry, ask; do not guess. Remote access follows the
HPC section of the global instructions (socket check first, never retry ssh after an auth
failure, file I/O only through the `-dm` host).

Poll the project's HPC jobs, reconcile the registry against the notes, and close only what
is provably finished.

# 0. Reconcile FIRST — halt on any drift

Before polling or writing anything:

1. Parse every ```sim-tracker``` block in **BOTH** `01_NotConfirmed/` and `02_Confirmed/`
   under `10_ResearchNotes/`. Scanning only NotConfirmed is what hid a stale `state: R`
   for four days on 2026-08-21: a note can be promoted while its jobs are still live.
2. Project the blocks with any non-verified-terminal job into the registry's shape.
3. Compare against `20_Progress/SimulationRegistry.md` on `tracker_id`, note path, `host`,
   `scheduler`.
4. **If anything differs, stop. Change nothing.** Report with these labels:
   `ORPHAN_NOTE` (block active, absent from registry) · `ORPHAN_REGISTRY` (row points at a
   missing block) · `PATH_MISMATCH` (note link no longer resolves) ·
   `STATIC_MISMATCH` (host/scheduler differ) · `DUPLICATE_TRACKER_ID`.
   Then offer a repair direction — rebuild registry from notes, or fix notes from scheduler
   evidence — and let the user choose. **Never pick silently.**

The registry is a derived index, never an authority. On disagreement the block wins unless
the scheduler says otherwise.

# 1. Poll

For each active tracker, read its job ids from the block (they are not in the registry —
duplicating state is what caused the incident). Poll only jobs not already
verified-terminal. `scheduler:` is authoritative; do NOT infer it from `host`.

# 2. Decide "finished without errors" — fail-closed, in this order

A job is successful only if **every** stage passes. Anything else leaves it registered.

1. **`verify:` block present and valid?** If not → `UNVERIFIABLE`. Never auto-close.
   Do not infer criteria from `note:` prose.
2. **Scheduler termination**
   - **PBS**: live `qstat`, then `qstat -xf`. Require `job_state=F` **and** numeric
     `Exit_status=0`. `wt`, negatives, any nonzero → failed. **History missing →
     `UNKNOWN`, not success** (`qstat -x` purges in ~2 days; fall back to the Gmail label
     `HPC notices/Nurion`, queried by display name).
   - **SLURM**: `squeue` first (presence = unfinished), then `sacct`. Require `COMPLETED`
     for the allocation *and every array element*, with **both** `ExitCode=0:0` and
     `DerivedExitCode=0:0`.
3. **Application checks** — exit 0 is NOT sufficient. These jobs pack many runs into
   parallel lanes and `wait`, so the exit status reflects only the last lane. Two real
   cases exited 0 while (a) all 18 runs died on a `KeyError`, 0 outputs, and (b) 8 of 12
   lanes were silently lost. So: expand `verify.outputs.glob` and require exactly
   `count`; same for `verify.logs`; grep every log for `reject_regex`. Zero matches never
   pass vacuously.

Record provenance on the job line when it passes: `verified: <source>@<timestamp>`.
A terminal state without provenance is `UNVERIFIED_TERMINAL`, not terminal.

# 3. Close

Close a tracker only when **all** its jobs are verified-successful, or the owner says so
explicitly. Owner closure needs a durable reason, or the record of *why* a failed campaign
vanished is lost:

```
closure: {mode: owner_override, by: <who>, at: <ts>, reason: "<non-empty>"}
```

Verified closure is recorded the same way with `mode: verified_success`. On close, move the
row to the registry's "Closed since" table; prune after a few days.

# 4. Write back

Write results into the block that owns the job. **If that note is in `02_Confirmed/`, do
not edit it** — a PDF was exported from it. Report the correction and let the user decide
whether to amend and re-export.

# 5. Diagnose failures

Cause → concrete fix, per failure. Remember: PBS `.e`/`.o` are usually empty (`-k eo` plus
a redirect); the real traceback is in the workdir as `./*_o.log`.

# Conventions

- Inside a `sim-tracker` fence, references to other notes are **bare dates** —
  `2026-03-20`, or `2026-03-20:multi-stealthy` for another project. No wikilinks: a fence
  is not indexed, so brackets never render and never survive a move.
- `tracker_id` is `<submission-date>-<campaign-slug>`. `workdir` is **not** unique — six
  blocks share one.
