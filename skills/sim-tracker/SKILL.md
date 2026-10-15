---
name: sim-tracker
description: Cheap status poll for Nurion PBS simulations, using the ```sim-tracker``` block embedded in a project's research notes as the source of truth. Reads PBS mail in Gmail first, falls back to one batched qstat, and writes back job state only on change. Never verifies, closes or moves anything; that is sim-update's job. Use when asked to "check sim status", "update the tracker", or on a schedule/loop.
---

# sim-tracker

You track Nurion PBS jobs for the current project. The research note is the database; you only
update it when something changed. Be cheap: one ssh, one qstat per run.

## Source of truth: the `sim-tracker` block

`<root>` is the project's Obsidian notes root, recorded in the project's `CLAUDE.md` (or
`AGENTS.md`); if none is recorded, ask. Research notes under `<root>/10_ResearchNotes/`
(`01_NotConfirmed/` and `02_Confirmed/`) carry a fenced block:

````
```sim-tracker
id: <submission-date>-<campaign-slug>
host: nurion
scheduler: pbs
workdir: <scratch dir>
status: running | complete | failed | stale
last_polled: <YYYY-MM-DD HH:MM>
verify: {...}          # optional; used by sim-update
jobs:
  - {id: 22460957, file: Circle-1.60-0.03-1.sh, state: Q, exit: -}
  ...
```
````

States: `Q` queued · `R` running · `F` finished · `exit` 0=ok. Parse THIS block, never the prose
table. Find target notes with `grep -rl '```sim-tracker' <root>/10_ResearchNotes` (default: the
note named in the user's request, else all notes whose block `status` is not
`complete`/`failed` and that have no `closure:` line). Only `scheduler: pbs` blocks are yours; for
`slurm` (Neuron) say so and point to sim-update.

## Status sources — Gmail FIRST, qstat only if needed

Every PBS script sets `#PBS -m abe -M phy000.kim@gmail.com`, so Nurion emails begin/end/abort per
job; the **end mail carries `Exit_status`**. All such mail is Gmail-labelled **`HPC notices/Nurion`**.
Gmail needs no Nurion socket and works from a remote routine — prefer it for exit/completion.

- **Primary (exit status, no socket):** search Gmail (Claude: `mcp__claude_ai_Gmail__search_threads`
  / `get_thread`; Codex: the Gmail plugin) with `label:"HPC notices/Nurion"` (the label's display
  name; the label-ID form returns nothing) plus the job id or job name. Parse the PBS body:
  `PBS Job Id: <id>.pbs`, `Exit_status=<N>`, and begin-vs-end (begin mail → at least `R`; end mail →
  `F` + that exit). Dedup by job id; the latest event wins. No Gmail tool → go straight to qstat.
- **Secondary (live Q→R, needs socket):** only if you also want live queue position OR a job has no
  mail yet — `ssh -O check nurion` then one batched `ssh -o BatchMode=yes nurion "qstat -x $IDS"`.
  Skip entirely if Gmail already resolved every job.

## Procedure (per note)

1. **Read** the block; collect all job IDs.
2. **Gmail pass:** search the `HPC notices/Nurion` label for these jobs; set `state`/`exit` from
   end/abort mail (`F` + exit), `R` from begin mail. Jobs with no mail stay as cached (`Q`/`R`).
3. **qstat pass (optional):** if any job is still unresolved *and* you need live state, socket-gate
   (`ssh -O check nurion`; if down, skip qstat — Gmail result still stands, just note socket was
   down for live state). One batched `qstat -x $IDS`; parse `S` column / `Exit_status`.
4. **Diff** fresh states vs the block's cached `state:`/`exit:`. If nothing changed → only bump
   `last_polled`; report "no change"; STOP.
5. **Write back** changed `state:`/`exit:` values with a single edit on the block. Recompute
   top-line `status:`:
   - any `Q`/`R` → `running`
   - any `exit` non-zero/non-`-` → `failed`
   - all `F` & every `exit` `0` → leave `status:` as is and report "ready for sim-update": exit 0
     is not success until sim-update's output and log checks pass.
6. **Peek at outputs only for `F` jobs** (skip running): one `ssh nurion-dm 'ls ...'` (file access
   goes through the data mover only) on the block's `verify` paths, else the workdir. Report any
   `F`-but-missing-output as a warning.

## Rules
- **Never edit a note in `02_Confirmed/`** (a PDF was exported from it): report the change instead.
- Never add `closure:`, never move notes between folders, never touch the registry. Verification,
  closure and the registry belong to sim-update.
- One ssh round trip for the poll; one more only if peeking at `F` outputs. No per-job ssh loops.
- Idempotent: unchanged states never rewrite job lines.
- `last_polled` always uses the real current time (`date '+%F %H:%M'`).
- Never block on OTP; socket-down is a clean stop, not an error. Never retry ssh after an auth
  failure.
- Terse output: one status line per job group + what changed. No filler.

## Triggering
You don't self-fire. A cron/`/loop` invokes you. Because the Gmail pass needs no socket, a **remote
`/schedule` routine is viable for status tracking**. Socket and qstat mechanics: the HPC section
of the global instructions.
