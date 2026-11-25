# Drafting sections from the research notes

The rules cited as "rule N" are the five rules in `SKILL.md`.

## Before prose

1. **The outline is the owner's.** Look for it in the request and in `<root>/40_Manuscript/`. For
   a whole paper, or a section whose message the owner has not stated, propose an outline and get
   it approved before writing prose: per section, one line per paragraph giving its message and
   the figure or equation it rests on. For a subsection whose content the owner has already
   specified, go to step 2. The story and the strength of each claim are not yours to choose.
2. **Find the record for every outline line.** Look first in `<root>/20_Progress/`, then in the
   dated notes it links to. Then search both dated-note folders for later notes on the same
   quantity: a note in `02_Confirmed/` is frozen, so its correction is a newer note, in
   `01_NotConfirmed/` at first and in `02_Confirmed/` once validated (rule 4).
   Read the note section itself, not a summary of it. A line with no record is a `[CHECK]` item:
   raise it now, while it is still an outline line.

## Writing

- **Order.** Write Methods and Results first, from the record. Then the Discussion. Write the
  Introduction, Abstract and Conclusions last, from what the finished sections claim.
- **Numbers.** One `% src:` comment per sourced statement (rule 1). Carry each number with the
  units, uncertainty and digits the note gives: no further rounding, no dropped error bar.
- **Equations.** Transcribe from the note symbol by symbol, then translate the notation. The notes
  use the vault macros (`\vect`, `\fn`, `\dd`, `\expval`); the paper uses its own preamble. Where
  the note's definition differs from the paper's (a factor of $2\pi$, a sign, a normalization),
  stop on that equation and report it; do not reconcile it silently. Do not re-derive results you
  were not asked to check.
- **What was done.** Describe it in the notes' operational terms. Where a note names a category by
  its criterion ("final energy below $10^{-14}$"), Methods keeps the criterion even if the paper
  also uses a short name. Do not borrow vocabulary from a method that was not carried out.
  Attempts that failed stay out unless the outline includes them, but never write a sentence that
  a recorded failure contradicts.
- **Methods describe what ran.** Parameter values, system sizes, sample counts, software and
  versions come from the run's own record: the note written at the time, a saved configuration, a
  log, the output's metadata. A script shows only what it does today: a default in it counts only
  where the record shows that the run used that setting; otherwise write a `[CHECK]`. Never write
  what such calculations usually use.
- **Statements about the literature** ("it is well known that", "Ref. [x] showed") need the cited
  paper itself opened, through Zotero (`zotero-obsidian-sync`). On a venue without the Zotero MCP
  tools, the local API serves it: `http://localhost:23119/api/users/<userID>/items/<KEY>/children`
  lists the PDF attachment, and `.../items/<attachment KEY>/fulltext` returns its indexed text in
  `content`. A
  `30_Literature/` note is a crude search result: use it to find the paper, not as evidence of
  what the paper says. If you could not open the paper, the sentence carries
  `\textbf{[CHECK: verify against the reference]}`. For cite keys see `figures-references.md`.
- **Journal limits** (abstract length, page or word count, required sections, keywords, data
  availability statement) change. Read the journal's current author page instead of recalling
  them, and tell the owner which limits you applied.

## Supplementary material

It is a separate main file (`SM/`), or an appended part of the main file if that is what the paper
already does. A result moves to it only by the owner's decision. Unless the paper already uses
`xr`, cross-references between the two documents are typed by hand: recheck each one after the
last edit to either document.

## Before reporting

1. Build clean (`SKILL.md`).
2. Compare the draft with the outline: every outline line is present, and nothing is present that
   the outline lacks.
3. Every claim in the Abstract and Conclusions is found in Results, in the same strength.
4. A value that appears more than once (abstract, text, table, caption) has the same digits and
   units everywhere: `grep` each one.
