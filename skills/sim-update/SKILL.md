---
name: sim-update
description: Reconcile a project's simulation registry with research-note trackers, poll PBS, Slurm, or explicitly identified local runs, verify terminal jobs against scheduler and application evidence, and close only proven or owner-overridden trackers. One script does every mechanical step; the model handles only the judgment items it lists. Use for /sim-update or requests to reconcile, verify, or close simulations. Use sim-tracker for a cheap status-only poll.
---

# sim-update

Reconciliation, polling, the scheduler gate, declared outputs/logs checks, and all writes of
`verified`, `closure`, and `SimulationRegistry.md` are mechanical: one script does them. Your work is
the judgment queue it prints. Never write `verified`, `closure`, tracker job states, or a registry
row move by hand; record every decision through the script, which re-runs the mechanical gates
first. The one manual exception is an owner-requested index repair ("Record repair").

```bash
S=~/.claude/skills/sim-tracker/scripts/simupdate.py
python3 $S [--project NAME | --root DIR | --all] [--dry-run] [--no-poll]       # reconcile, poll, verify, close
python3 $S accept KEY --checks "<what you checked and found>" [--mail FILE] [--archived] [--ledger-checked]
python3 $S override KEY --by OWNER --reason "<decision and unresolved evidence>"
python3 $S register KEY
```

`accept`, `override` and `register` take the run's scope flag (`--project NAME`, `--root DIR` or
`--all`), written after the subcommand and KEY: a flag placed first is parsed as a new `run`. Pass
it whenever the run needed it; if KEY exists in several projects, use `--project` or `--root`.

## Procedure

1. **Scope.** With no flag the project comes from the current directory (research-note's
   `find_root.py`); pass `--project NAME` when the user named one; if it exits 1 with the root
   unresolved and nothing was named, use `--all`. If a project has no registry, ask the owner where it
   is; do not create one.
2. **Run it** and relay its report. Exit 3 means an SSH endpoint was unavailable: ask the user to open
   it (`ssh <host>`, OTP or Duo, leave it open), never retry SSH yourself, then rerun.
   A default run is write-enabled and closes whatever passes, and the script sees a controller
   only when the block declares one (the controller item in step 4). So unless you have already
   read the notes whose trackers are in scope, run `--dry-run` first and read the note text
   around each tracker it would close or lists as `JUDGMENT NEEDED`. If the prose says a tracked
   job submits successors and the block does not declare that, run neither a write-enabled `run`
   nor `accept` for it: report the tracker and have the owner declare the controller in the block
   (or decide otherwise) first.
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
   - `JUDGMENT NEEDED`: work through every item below, then run the `accept` command it prints, as
     `python3 $S accept …` with the run's scope flag (the printed line omits both).
4. **Judgment items** (read-only on the file endpoint: `nurion-dm`, `neuron-dm`, `della`). The
   script's SSH safeguards do not cover commands you type: before any manual remote command run
   `ssh -O check <host>`, and if the master is not running, ask the user to open it and stop.
   Give every manual `ssh` the poller's master-only options, so that a master that drops after
   the check fails without any login attempt (repeated failed logins risk account lockout), and
   stop at the first connection failure:
   `-o BatchMode=yes -o ControlMaster=no -o PubkeyAuthentication=no -o PasswordAuthentication=no
   -o KbdInteractiveAuthentication=no -o GSSAPIAuthentication=no -o HostbasedAuthentication=no`.
   - **verify items needing judgment** (`additional`, `case_manifest`, `expected_case_counts`,
     `role_outputs`, hashes, and the like): perform every stated check; none is optional commentary.
     For multi-case lane workflows the contract must name a case manifest and expected case IDs or
     counts; match actual completed case identities to it. A present marker or output file alone
     does not prove valid content. If any check fails or cannot be performed, do not accept: report
     the tracker as failed or `UNVERIFIABLE` with the missing evidence.
   - **no fresh scheduler record** (history purged). Either (PBS jobs only, and only where the
     venue has Gmail access; elsewhere report the exact job IDs and ask the user for the mail
     records) search Gmail label **`HPC notices/Nurion`** (by display name) for each exact job
     ID; only end/abort mail with an
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
     ledger; a controller described only in the note's prose is yours to notice before any
     `accept`, see step 2): resolve the
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

## Reference (read when needed)

Both files are in the `sim-tracker` skill, beside the scripts.

- `../sim-tracker/references/simupdate-contract.md`: what `simupdate.py` does (reconcile and drift
  codes, the scheduler and application gates, verification and closure, registry writes). Read it to
  explain a report line or before changing the script; change the script and that file together.
  `simupdate.py --selftest` runs an offline end-to-end check.
- `../sim-tracker/references/tracker-block.md`: what a submitting session or launcher must record
  when it creates a tracker (registration with `register KEY`, the `verify` contract, case
  manifests, the local wrapper receipt, an example block).
