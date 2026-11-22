---
name: manuscript
description: Write and revise the owner's physics papers in LaTeX (REVTeX, BibTeX, an Overleaf git clone) — draft sections from the project's research notes, prepare figures and captions, bring references from Zotero into the paper's .bib, revise after referee reports, build the marked-changes PDF, and write replies to referees, letters to the editor and cover letters. Use whenever a session edits or builds a paper, its supplementary material, its .bib or a reply letter in a project's `40_Manuscripts/` folder, or the user says "draft the Results section", "write up the paper", "add this reference", "fix the caption", "the referee reports came in", "reply to the referees", "make the latexdiff" or "cover letter", even when they never say "manuscript". Not for the arXiv source package of an accepted paper (prepare-arxiv-submission), daily research notes (research-note), reports and slides, or the CV publication list.
---

# manuscript

A manuscript is the owner's public claim, written with coauthors. Three things set it apart from
other writing here: every statement must trace to the research record, the source is shared with
coauthors through Overleaf, and what reaches a journal or a referee cannot be taken back.

## Find the paper and its record

- **LaTeX source:** `~/10_Projects/<bucket>/<project>/40_Manuscripts/<paper>/` (older projects:
  `40_Manuscript/`, `manuscripts/`). Each paper is its own git repository whose `origin` is
  Overleaf. Find it on the filesystem and in the project's `CLAUDE.md`. When a folder holds
  several candidates (`paper.tex`, `paper_orig.tex`, `main_v9.tex`), ask which is current; a file
  name does not tell you.
- **Research record (vault):** `python3 ~/.claude/skills/research-note/scripts/find_root.py
  --project <vault folder>` prints `<root>`; exit 1 lists the candidates, so ask. The vault folder
  can be named differently from the code folder (`Plasmonic` for `Plasmon`). Under `<root>`:
  `20_Progress/` (results organized for publication), `10_ResearchNotes/02_Confirmed/` and
  `01_NotConfirmed/` (dated notes), `30_Literature/`, and `40_Manuscript/` (the owner's own
  outlines and notes on referee comments).
- **Data and figure scripts:** `20_Data/`, `25_Figs/`, `30_Codes/` of the same project.
- **Zotero collection:** its key is in the project's `CLAUDE.md`.

## The Overleaf repository

- Before the first edit run `git status`, then `git pull --ff-only`. Uncommitted changes are the
  owner's work in progress: leave them. If the pull fails or refuses (it would overwrite a local
  change, or there are local commits Overleaf does not have), stop and report. Never stash,
  rebase, merge or reset in a paper's repository on your own. An older paper folder that is not a
  git repository has no undo: tell the owner before editing in it.
- Commit and push only when the owner asks. A push changes what every coauthor sees on Overleaf.
- The remote URL contains the owner's Overleaf token. Never let it reach output, a note, a log or
  a brief: no `git remote -v`, no `git config --get remote.origin.url`, no reading `.git/config`.
- Edit only what the task names. The rest is coauthors' text: no rewording in passing, no
  re-wrapping of paragraphs (it turns one changed word into a changed paragraph in the diff), no
  renamed labels or macros, no reordered preamble.

## Class and build

- Follow the paper: its class and options (`revtex4-2` with, for example,
  `aps,prb,reprint,superscriptaddress`), its bibliography mechanism (BibTeX through
  `\bibliography{...}`; REVTeX loads natbib itself), its macros and its notation. Do not add a
  package, switch to biblatex, set the page geometry, or define a second macro for a quantity that
  already has one. A new paper starts from the preamble of the owner's most recent paper in the
  same line of work (ask which), not from a generic template.
- Build into `build/`, never beside the sources:
  `latexmk -pdf -interaction=nonstopmode -outdir=build <main>.tex`. If `build/` is not in the
  paper's `.gitignore`, say so in the report.
- The build is clean when `grep -a -E '^! |undefined|multiply defined' build/<main>.log` prints
  nothing. Report overfull boxes in lines you wrote.

## Five rules for every stage

1. **Nothing enters from memory.** Each number, formula, parameter value, and statement of what
   was done or found comes from a file opened in this session: a research note, a data file, a
   script, or the cited paper itself. Record the source in a TeX comment on the line above, with
   paths relative to the project folder and never absolute:
   `% src: note 2026-10-03, "Fit results"`, `% src: data 20_Data/sweep_phi2/fit.csv`,
   `% src: code 30_Codes/estimate_eps_eff.py`. These comments are for the authors. A journal or
   arXiv upload publishes the TeX source with its comments, so when a submission is being
   prepared, remind the owner that they are still in the source.
2. **What has no source stays visibly open.** Write `\textbf{[CHECK: what is missing]}` in the
   text, never a plausible value, citation or sentence. The marker needs no package and shows in
   the PDF. List every marker in your report. `grep -rn --include='*.tex' '\[CHECK' .` must print
   nothing before anything is submitted.
3. **Status carries over.** A result taken from `01_NotConfirmed/`, or labeled a hypothesis in its
   note, is reported to the owner as unconfirmed when you use it, and its wording in the paper is
   no stronger than in the note: a hypothesis does not become "we show".
4. **Two notes, two values.** When notes disagree on a quantity, use the later note and report
   both values and both dates. Never average them and never choose silently.
5. **The owner decides the science and every outward step:** the claims and their strength, the
   author list, the journal, suggested or excluded referees, whether to concede a referee's point,
   and every submission, upload, email and push. Draft these for the owner. Never send, submit, or
   write them as settled.

## Stages

Read the reference for each stage the task touches before starting it, and leave the others
unread.

| Stage | Read |
|---|---|
| Drafting sections from the research notes | `references/drafting.md` |
| Figures, captions, the `.bib` and citations | `references/figures-references.md` |
| Referee reports: revision, the marked-changes PDF, replies, letters to the editor, cover letters | `references/revision-replies.md` |

Packaging an accepted paper for arXiv belongs to `prepare-arxiv-submission`.

## Report

End with: the files changed; the build result; every `[CHECK]` marker left and what would close
it; every unconfirmed or superseded value used (rules 3 and 4); and the decisions waiting for the
owner. Say plainly what you did not verify.
