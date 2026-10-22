**Verdict: no blocking issue from round 1 remains.** B1, B2, and B3 are closed in the installed files, and the local receipt and identity clarifications are present. Everything below is nonblocking and fails closed.

**Round 1 status**

- **B1 (array indices)** is fixed. Both skills now query `JobID` with `--array` and state that `JobIDRaw` cannot carry an index (`skills/sim-tracker/SKILL.md:26`, `skills/sim-update/SKILL.md:29`). Expected coverage comes from the recorded `array:` expression or a manifest, throttle suffixes are ignored, and unknown coverage leaves the parent unresolved.
- **B2 (cached Slurm state as verification)** is fixed. Gate 2 requires a fresh `sacct` query in the current run and names the cached fields that are not evidence (`skills/sim-update/SKILL.md:37`). Endpoint unavailable fails the gate.
- **B3 (vacuous closure)** is fixed. Closure needs nonempty `jobs`, and `NO_JOBS` blocks stay open in both reconcile and closure (`skills/sim-update/SKILL.md:22`, `skills/sim-update/SKILL.md:49`). sim-tracker never writes to a block with no unverified job, so it cannot touch one.
- **Identity and receipt** now require both hostname and machine ID, a run-ID-bearing receipt filename, and boot ID matched to the launch record rather than the current boot. The reader also checks the receipt's `run_id` field, so a stale receipt with the old run ID cannot pass even if a filename collided.

**Nonblocking findings, severity ordered**

1. **Medium. PBS gate lacks the explicit "fresh query" clause that Slurm received.** `skills/sim-update/SKILL.md:37` says PBS needs `F` and exit `0` "from qstat history or matching end mail" but does not say cached `state`/`exit` from an earlier sim-tracker poll are not evidence. Trigger: a job polled weeks ago with F/0, qstat history since purged, Gmail unavailable in this session. Step 1 at line 28 already returns `UNKNOWN` for that case, so the designed outcome is fail-closed, but the asymmetry with the Slurm sentence invites reading the cached fields as "from qstat history." Minimal fix: move the sentence "Cached `state`, `exit`, ... from an earlier poll are not verification evidence; if the endpoint is unavailable, this gate fails" so it applies to all three scheduler types.

2. **Low. `sacct --array` is version dependent.** `skills/sim-tracker/SKILL.md:26` and `skills/sim-update/SKILL.md:29`. I could not check the live Slurm docs here and am relying on the task's statement that the option exists. On a cluster whose sacct predates it, the command errors, which yields no evidence and `UNKNOWN`. That is safe but makes every poll on that cluster fail. Minimal fix: add "if `--array` is rejected, omit it; sacct still lists started tasks as `<id>_<index>` rows and collapses only pending tasks into `<id>_[...]`, which count as not complete."

3. **Low. The `start_ticks` field-counting wording can be misread.** `skills/sim-tracker/SKILL.md:27` and `skills/sim-update/SKILL.md:30` say "field 22 counted after its last `)`." Field 22 in proc(5) numbering is the 20th token after the `)`. A wrapper that takes the 22nd token after `)` records `rss`, which changes over time, so the live-process check would report `UNKNOWN` instead of `RUNNING`. No false success is possible, but the wording should read "the 20th whitespace-separated token after the last `)`, which is field 22 in proc(5) numbering."

4. **Info. "Allocation" row for arrays.** Gate 2 at `skills/sim-update/SKILL.md:37` requires "the allocation and every expected array task." sacct emits no bare parent row for an array, only `<id>_<index>` rows plus their steps. An implementer should read "allocation" as the task rows themselves for arrays. Worth one clarifying clause, not a correctness risk.

**Assumptions**

- I reviewed only the installed skill text and did not run any scheduler or SSH command.
- The sacct `--array` semantics are taken from the task brief, not verified against the live documentation.