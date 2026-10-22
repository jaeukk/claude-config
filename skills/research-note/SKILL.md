---
name: research-note
description: Write, append to, or revise a project's daily research note (`<root>/10_ResearchNotes/01_NotConfirmed/YYYY-MM-DD.md` in the Obsidian vault) and its literature companions in `<root>/30_Literature/`. Use whenever a session records work in the vault — "log this in today's note", "write up the simulation", "add the discussion with X to the research note", "record the lit search", "put the results in the note", or any Edit/Write under `10_ResearchNotes/` — even when the note is a side task of a coding, data, or HPC session and the user never says "research note". Not for wiki pages (`20_Topics`), source summaries (`40_Resources`; paper-reviewer, book-summarizer), the personal daily log (`60_Logs/01_Daily`), or polling `sim-tracker` blocks (sim-tracker, sim-update).
---

# research-note

A research note is the dated lab notebook of one project and its sub-projects: simulations, code, data acquisition, literature-search plans, discussions with colleagues, trial and error. It is deliberately raw and loosely organized. Its job is **evidence and reproducibility**: a reader with no session context must find the data and scripts behind every result and rerun them. In LLM-wiki terms it is a raw source, not a wiki page; condensed knowledge lives elsewhere (see *What does not go here*).

Notes are exported to PDF and filed in a government research archive as proof of research activity. The record must be honest about what was done and when; nothing inflated, nothing backdated.

## Locate

First command, from wherever the session runs:

```
python3 <skill dir>/scripts/find_root.py --note            # prints today's note path
python3 <skill dir>/scripts/find_root.py --note --project <name>   # when the user names a project
```

Exit 0: write there (it creates nothing; the file may not exist yet). Exit 2: today's note is already in `02_Confirmed/`, frozen (see below). Exit 1: unresolved; it lists the candidate projects on stderr, so ask the user which one and rerun with `--project`. Never guess a project from the conversation topic.

How it decides, in order: `--project` (vault folder or code-side folder name) → cwd inside the vault (the `10_Projects/<bucket>/<Project>` prefix) → a code-side `CLAUDE.md`/`AGENTS.md`/`README.md` above cwd with a `Notes root (WSL)` row or `notes_root:` → a vault project `README.md` whose `code_root:` contains cwd. `--project` also accepts `<bucket>/<name>` when a name exists in two buckets. The resolver only finds projects that already have a vault folder; a project with none yet is scaffolded first (the `project-manager` agent does this), and a code-side project gets one of the two markers so later sessions resolve it without `--project`. The vault base itself comes from the `zotero-obsidian-sync` rules (`$OBSIDIAN_VAULT`, the pin file, the Drive glob), never a hardcoded Windows user segment.

- `<root>` is the project folder the path shows; a date lives in exactly one of `01_NotConfirmed/` and `02_Confirmed/`. Not every date has a note (working days only).
- `02_Confirmed/` holds notes already published to the archive. **Never edit them.** A correction is a new dated note in `01_NotConfirmed/` that links to the old one.

## Create today's note

Obsidian's Templater normally creates it from `90_Templates/ResearchNote.md`. When creating from the terminal, reproduce the template's output rather than inventing a layout:

1. Confirm the file does not exist yet (another session may have created it minutes ago). Copy the head of the most recent note in the same folder: frontmatter through the end of the `# Objectives` dataviewjs block. If the folder is empty, render `90_Templates/ResearchNote.md` by hand: its frontmatter keys, `plan:` pointing at `<root>/ResearchPlan`, and the Objectives block with that plan path.
2. Set `created:` and the H1 (`# YYYY-MM-DD -- Research Note`) to today's date, `last_modified:` to now, `previous_note:` to that note as a quoted wikilink (`"[[2026-10-21]]"`), or `""` for a project's first note. Keep `project:`, `plan:`, `preamble:` as they were.
3. Add the empty sections in template order: `# Context`, `# Plan`, `# Notes`, `# Findings`, `# Next Steps`, `# References`.

The date is the note's identity. Never rename a note or change its `created:` or H1 date. Work done on a later day about an earlier day goes in the later day's note with a link back.

## Append or revise: several sessions share one note

Several sessions (Claude, Codex, different working directories) append to the same note during a day, and Google Drive syncs the file underneath them.

- Before a session's first edit, read the whole note and confirm that what was written earlier today (by you or another session) is still there; Drive reverts arrive minutes to hours after the write. Report a gap before appending. Re-read again immediately before each edit; never edit from an earlier read.
- Append to an existing note with an anchored Edit under the right `#` section, never a whole-file Write.
- New content is a new `##`/`###` subsection. Never add frontmatter, a second H1, or a top-level `#` heading outside the template's set.
- Revise only the section the user names. Other sections and every ```sim-tracker``` fence belong to their authors (the fences to sim-tracker and sim-update).
- Update `last_modified:`. Then re-read the edited region once to confirm it persisted; Drive has silently reverted fresh edits before.
- A later addition inside an existing subsection carries its own date: `(added 2026-10-23)`.

## Write

**Concise.** Fragments and bullets over sentences, tables over prose, never restate what a table or figure already shows. One exception: a ```sim-tracker``` block stays verbose and self-contained because the polling agent reads it without the surrounding note.

**Plain words, no overstatement.** Describe the operation itself ("fit the even-indexed $\lambda$, predict the odd-indexed $\lambda$"), not session jargon, code identifiers, or shorthand; define an unavoidable term where it is used. Do not borrow vocabulary from a method that was not carried out: no "training" or "held-out" for a least-squares fit. Name planned methods only as plans. American spelling.

**Math.** LaTeX only, `$inline$` and `$$display$$`, `\tag{}` never `\eqno`. Follow `00_Index/Math_Notation.md`: macros that name the object (`\vect{r}`, `\dd{x}`, `\fn{f}{x}`, `\expval{X}`); the note's `preamble:` property loads them. Show a derived number as its construction, not only its value:

```
$$K = \frac{\pi}{2\cdot 26\cdot 1560}\left(\frac{2600}{8580}\right)^2 \times 2 = 7.11\times10^{-6}\ \mu\mathrm{m}^{-2}$$
```

Define a generating function (`$f(x,y) = [1+\cos K(x^2+y^2)]/2$, level cut at $1/2$), not only its consequences.

**Sections.**
- `# Context`: what is known going in. Link the previous note.
- `# Plan`: what will be tried today.
- `# Notes`: the work, in order. Enumerate the design axes of an experiment grid as numbered lists before any prose ("40 samples = 2 ratios × 4 masks × 5 arrangements", then one list per axis). Failed attempts stay in: a trial that did not work is a result.
- `# Findings`: the physics, stated as physics. Estimator-noise hedges and caveats that are really future work go to Next Steps.
- `# Next Steps`: the **only** place for next-step items, with one exception: a simulation batch's processing plan stays under its ```sim-tracker``` block (below), and Next Steps points to it. One top-level section near the end; `##` subheadings per part are fine. Each item links to the section that raised it (`[[#II.3 Masking|II.3]]`, or `[[2026-08-19]] I.1` for another note) so a hoisted item keeps its context. Pointer sentences ("jobs are tracked in the sim-tracker block of …") are not next steps.
- `# References`: papers, concept wikilinks (`[[Nyquist_Shannon_Sampling_Theorem|Aliasing]]`), related notes.

**Plans and tasks live in `<root>/ResearchPlan.md`**, the project's single task list (task syntax: the one stated in the ResearchPlan template header). The note's `# Objectives` block only mirrors it through dataviewjs. So: `# Plan` names which ResearchPlan tasks today serves; a `# Next Steps` item that becomes real work is added to ResearchPlan.md as a `#task` line (with a link back to the note), not kept as a private to-do in the note. Edit ResearchPlan task lines in place (reschedule ⏳, tick ✅); never move a line or change its `^id`, because daily notes block-link into them.

**Project vocabulary.** Read the project's `CLAUDE.md` for required terms. Multistealthy: `$\otimes$` for convolution; call these models disordered or hyperuniform (HU), never SHU or stealthy.

**Provenance sidebars** go in `>[!info]` callouts: per-sample file inventories, formats, who ran what. Keeps the main flow on the science.

**Discussions** with colleagues: date, who, what was decided, what stays open. Attribute each claim to the person who made it.

## Point at data and code: the two trees

The vault describes; the code side (`~/10_Projects/<slug>/`) holds data, scripts, and runs. Every result names what produced it.

- Pointer form (policy §5): `code: 30_Codes/dithering2/Lloyd.py`, `data: 20_Data/color_mixing/2026-10-15_run/`, `run: experiments/2026-10-21_matched-tetramers/`, relative to the project's `code_root:` in the vault project `README.md` frontmatter. Plain text in backticks, never an Obsidian link, never an absolute path (`/home`, `/mnt`, `C:\`). Remote HPC paths appear only inside sim-tracker fences.
- Name the post-processing script and the dataset directory together; the script sits beside its data (global research-code workflow).
- The two trees share one numbering: a two-digit prefix means one thing on one side and nothing else on the other. Do not create data or figure folders in the vault. Where a prefix already collides (`20_Progress` vs `20_Data`, `25_Figures` vs `25_Figs`), say so in the note and leave renaming to the user.

**Figures.** Copy a frozen snapshot into `<root>/10_ResearchNotes/_assets/<project>_YYYYMMDD_<n>.png` at citation time; code-side figures change, the note must not. Take the next unused index. Embed, blank line, then the caption **directly below**: `**Fig. 3** — …`; a blank line between consecutive embeds. Group embeds by what they show (Figs. 3–4 under one caption, Fig. 5 alone). Name the script that drew it.

**Links.** Another research note in the same project: full vault path, bare date as display text, `[[10_Projects/10_Active/<Project>/10_ResearchNotes/01_NotConfirmed/2026-08-20|2026-08-20]]`. The short `[[2026-08-20]]` is safe only for a sibling in the same folder: every project and the daily log (`60_Logs/01_Daily/`) share these basenames, so a bare link can resolve to the wrong file once a note moves to `02_Confirmed/`. Inside a table cell escape the pipe: `[[…/2026-08-20\|2026-08-20]]`. Inside a ```sim-tracker``` fence, no wikilinks at all: bare `2026-08-20`, or `2026-08-20:multi-stealthy` for another project.

**Simulations.** Each batch gets one ```sim-tracker``` YAML block (schema in the `sim-tracker` skill) and, directly below it, its own processing plan: what to do with the outputs once the jobs finish. Job state in the block is written only by sim-tracker and sim-update.

**Literature.** Crude literature-search results go in `<root>/30_Literature/<Topic>.md` (new file names per policy §4: no spaces or hyphens, at most four words) under a date heading `## YYYY-MM-DD`. The day's note transcludes that heading instead of copying it, by full path: `![[10_Projects/10_Active/<Project>/30_Literature/<Topic>#2026-10-22]]`. One copy of the text: the raw search stays in `30_Literature`, condensed knowledge later goes to the wiki.

## What does not go here

- `<root>/20_Progress/`: results organized over weeks and months. Finalized simulation setups, settled parameters, long data-acquisition campaigns, the publication ledger. The daily note links to them; operational detail (queue failures, debugging) stays in the daily note. Plasmonic keeps two: `SimulationProgress.md` (results ledger for publication) and `SimulationParameters.md` (settled parameters).
- `20_Topics/`, `40_Resources/`: wiki pages and source summaries (wiki-ingest, paper-reviewer).
- `60_Logs/01_Daily/`: the personal daily log and pomodoro records.

## Verify

After every edit run

```
python3 <skill dir>/scripts/check_note.py <note.md>
```

`<skill dir>` is where this SKILL.md was loaded from (`~/.claude/skills/research-note` on Claude Code, `~/.codex/skills/research-note` on Codex); the same for `find_root.py` above.

Fix every ERROR on lines you wrote; report, do not fix, findings on lines you did not touch (legacy notes have their own history). Judge each WARN. It checks only the mechanical rules: filename and date identity, single frontmatter, allowed H1 set, one Next Steps, absolute paths, link display text, wikilinks in fences, caption placement, `\eqno`. Concision, wording, and physics-not-error-analysis are checked by rereading the diff as a stranger would.

## Provisional rules (author's additions, 2026-10-22)

Not stated by the owner or a memory; strike or confirm, then delete this section: the anchored-edit protocol and "revise only the named section"; `(added YYYY-MM-DD)` on later additions; promoting Next Steps items into ResearchPlan; applying the Plasmonic (Next Steps, links) and Multistealthy (axes, captions, callouts) conventions to every project; the `<project>_YYYYMMDD_<n>` snapshot name (from the vault manager's area H); attributing discussion claims by person; "remote HPC paths only inside sim-tracker fences".
