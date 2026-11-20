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
   authenticate with the OTP or Duo, leave it open). Never retry SSH yourself: repeated failures risk
   account lockout.
4. **Hand-offs.** Lines ending in `sim-update` (all jobs terminal, failed, NO_JOBS, every job
   verified, excluded by the quarantine screen) are sim-update's job. Polling never verifies or closes.
5. A status request does not authorize launching, repairing, or resubmitting simulations.

## Reference (read when needed)

- `references/simtrack-contract.md`: what `simtrack.py` does (scope, strict parsing and quarantine,
  endpoints, PBS, Slurm and local rules, evidence, writeback). Read it to explain a report line or
  before changing the script; change the script and that file together. `simtrack.py --selftest`
  runs an offline end-to-end check.
- `references/tracker-block.md`: how a session that submits jobs creates and registers a tracker
  block (required fields, `verify`, the local-run receipt, an example). Polling never creates one.
