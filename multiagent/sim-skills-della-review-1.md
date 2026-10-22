Both files read in full. Findings below, ordered by severity. I did not edit anything, run remote commands, or touch schedulers.

## Blocking

**B1. Slurm array coverage cannot be computed with the prescribed sacct fields.**
- Trigger: any Slurm array job on Neuron or Della, ordinary use.
- Impact: both skills say to expand expected indices and compare them with returned task IDs, but the prescribed format uses `JobIDRaw`, which prints each task's raw numeric ID with no `_<index>` suffix. The index comparison is unexecutable as written, so the agent either stalls at "unresolved" forever or improvises a raw-ID-to-index mapping. Also no `array:` field is defined anywhere in the block schema, so the "expected" side has no agreed home.
- Evidence: `/home/jaeukk/.claude/skills/sim-tracker/SKILL.md:26`, `/home/jaeukk/.claude/skills/sim-update/SKILL.md:29`.
- Fix: in both lines replace `JobIDRaw` with `JobID` (or append `ArrayJobID,ArrayTaskID`), and add one sentence: "Record the submitted array expression on the job row as `array: <expr>`; `JobID` carries the `_<index>` suffix needed for coverage, `JobIDRaw` does not."

**B2. Slurm verification gate names no evidence source, so cached poll results can satisfy it.**
- Trigger: one ordinary interruption. The scheduler login (`neuron` or `nurion`) is unavailable or stuck on OTP during sim-update, while the `-dm` file endpoint works. A prior sim-tracker poll already wrote `state: COMPLETED`, `exit: 0:0` on the job row.
- Impact: gate 2 for PBS and local states its source (qstat history or end mail, wrapper receipt), but the Slurm clause only lists required values. An agent can read them from the block, pass gate 3 on `-dm` files, and write `verified` and a `verified_success` closure from cached allocation state. The context explicitly says cached Slurm state is insufficient. The risk compounds because sim-tracker never defines how "unresolved" array coverage is recorded on the row, so a parent with missing tasks can sit in the block as a bare `COMPLETED`.
- Evidence: `/home/jaeukk/.claude/skills/sim-update/SKILL.md:37` (no source named for Slurm), `/home/jaeukk/.claude/skills/sim-tracker/SKILL.md:26` (no field for coverage result).
- Fix: append to sim-update line 37: "This gate is satisfied only by scheduler or receipt evidence observed in this run; cached `state`, `exit`, or `derived_exit` fields from an earlier poll are not evidence, and an unreachable scheduler endpoint leaves the gate failed." Append to sim-tracker line 26: "When writing an array result, record `tasks_expected` and `tasks_missing` on the job row; never write a bare `COMPLETED` for a parent with missing tasks."

**B3. A block with an empty `jobs` list closes as `verified_success` vacuously.**
- Trigger: a tracker block drafted before submission, or one whose job rows were removed. Plausible in a research-note workflow.
- Impact: step 0 treats it as fully verified (no non-verified job), step 1 skips it, step 2 runs no gates, and step 3 closes it and adds a closed registry row. False closure with zero evidence.
- Evidence: `/home/jaeukk/.claude/skills/sim-update/SKILL.md:22`, `:26`, `:49`.
- Fix: at line 49 change to "Close as `verified_success` only when `jobs` is nonempty and every listed job has verified success and ..."; add "A block with no job rows is `NO_JOBS` and stays open."

## Nonblocking

- **Receipt boot ID after reboot.** sim-tracker line 39 says identity fields must match "the launch record and verified execution machine." A strict reading requires the receipt's `boot_id` to match the current boot, which rejects every valid receipt after a reboot (permanent `UNKNOWN`, fail-safe but unexecutable). Clarify: `pid`, `boot_id`, `start_ticks` match the launch record; only `machine_id` must also match the current machine. Same clarification at sim-update line 45.
- **Fixed receipt filename.** The example at sim-tracker line 63 uses `run.receipt.json` in the workdir. With `RENAME_NOREPLACE`, a second run in the same workdir can never commit, and the stale receipt mismatches forever. Put `run_id` in the receipt filename.
- **Undeclared controllers.** Gate 4 (sim-update line 39) applies "if a controller submits successors," but no block field marks a job as a controller or lists a ledger path. An undeclared controller passes gates 1 to 3 and closes while successors are untracked. Define `controller: {ledger: <path>}` or `successors:` so "explicitly unresolved" (sim-tracker line 8, sim-update line 26) is machine-checkable.
- **`squeue -j` with purged IDs.** When any listed ID is no longer known to the controller, squeue errors for the whole list and prints nothing. Add to sim-tracker line 26 and sim-update line 29 that a squeue error is not evidence and sacct decides state.
- **`/proc/<pid>/stat` field 22.** The `comm` field may contain spaces or parentheses, so naive whitespace splitting miscounts. Say "field 22 counted after the last `)`" at sim-tracker line 27 and sim-update line 30.
- **Step 0 ambiguity.** "non-verified-terminal job" at sim-update line 22 can be read as "terminal but unverified," which would exempt running blocks from needing registry rows. Rephrase as "any job that is not a verified terminal job, including queued or running jobs."
- **Laptop identity source.** Both tables assume Linux `/etc/machine-id` and `/proc`. The laptop's OS is not stated. If it is not Linux or WSL, the local contract is unexecutable for it. Also, a WSL distro exported from the office and imported on the laptop carries the same machine ID. Require hostname and machine ID to both match for direct reads, not only for SSH reads (sim-tracker line 20, sim-update line 18).
- **sim-tracker lacks the coverage fallback.** sim-update line 29 says unknown expected coverage is unresolved; sim-tracker line 26 omits that sentence. Add it for consistency.
- **Multistealthy drift.** Because registry paths there are already stale, sim-update will halt at step 0 for that project on every run until the owner chooses a repair direction. This is the intended behavior, not a defect, but the user should expect it.
- **WSL background runs.** The WSL2 VM stops when the last session closes, which kills a wrapper without a receipt and leaves the run `UNKNOWN` forever. This is the correct fail-safe outcome. The launcher docs should warn about it.

Confirmed-note immutability, the `id`/`tracker_id` alias rule, the Della single-endpoint mapping, the reverse-tunnel caveat for `laptop`, PID reuse handling, and the "disappearance is not completion" rules are consistent across both files and I found no false-success path through them.

Assumptions: the laptop runs Linux or WSL as the files imply, and tracker blocks are normally created at submission with job rows. If the second assumption always holds, B3 can be downgraded to nonblocking. If a round 2 is used, it should check only the three blocking fixes above.