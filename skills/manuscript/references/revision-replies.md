# Referee reports: revision, replies, letters

The rules cited as "rule N" are the five rules in `SKILL.md`.

## Establish the inputs

- **The reports and the editor's letter, as received** (PDF, or the text of the email). If they
  are not yet in the paper's `reply/` folder, save them there unchanged. Extract text with
  `pdftotext -layout`; if it comes out empty or garbled (a scan), read the page images instead of
  guessing at the words.
- **The submitted version:** the exact source the referees read. Ask the owner for its commit, tag
  or archive (a `version1.tar.gz`, an arXiv version). Do not assume it is the previous commit or a
  file named `_orig`.
- **The owner's notes on the reports,** if any, in `<root>/40_Manuscript/` in the vault: what to
  change and what to rebut, comment by comment. They outrank your own reading of a report.

## Sort every comment before editing

Number every point the editor and each referee make (`E.1`, `R1.1`, `R1.2`, `R2.1`). Split a
paragraph that raises two issues. Keep general remarks and praise as items: they get a one-line
answer. Give each item one class:

| Class | Meaning |
|---|---|
| change | text, a figure or a reference changes; say where |
| clarify | the paper already says it; point to where, and consider making that place clearer |
| new work | needs a calculation, simulation or data that the record does not contain |
| disagree | the authors keep their position |

Show the owner this table and wait until the classes are confirmed, unless the owner's notes
already settle them: calling a comment `change` already concedes it. Until then, edit only what
alters no claim (a typo, a wrong reference number, a missing definition). Then draft the `change`
and `clarify` items. For each `new work` and `disagree` item, and wherever two referees pull in
opposite directions, put the decision to the owner with the options and draft nothing in the
paper until it is made. Do not start new simulations, and do not concede or reject a scientific
point, on your own.

## Revise the paper

- One comment at a time, with the smallest edit that answers it. Prefer replacing a weak sentence
  to appending a paragraph.
- Rules 1 to 4 apply to every added sentence and number. A referee's request is not a source: a
  result a referee asks for enters only from the research record, after the owner has had it
  produced.
- Do not mark changes by hand in the source (`\textcolor`, `\st`) unless the paper already does or
  the journal asks for it. The marked copy is generated:

  ```
  git show <submitted commit>:<main>.tex > <scratch dir>/old.tex   # or the file from the submitted archive
  latexdiff <scratch dir>/old.tex <main>.tex > <scratch dir>/diff.tex
  latexmk -pdf -interaction=nonstopmode -outdir=build <scratch dir>/diff.tex   # run in the paper's folder
  ```

  Keep `old.tex` and `diff.tex` in a scratch folder of your own, never in the paper's folder: the
  redirection would erase an existing file of that name, and a generated file does not belong in
  the Overleaf repository. Do not edit `diff.tex` by hand: the final recheck generates it again.
  Run `latexmk` in the paper's folder so that it finds the figures and
  the `.bib`; the marked copy is `build/diff.pdf`. Look at it and confirm that the changes show
  (added text blue and underlined, deleted text red and struck out). `latexdiff` cannot see a
  figure whose file was replaced under the same name: name every replaced figure in the letter to
  the editor's list of changes, and tell the owner that these figures carry no mark in the marked
  copy, in case the journal wants every change shown there. A paper split over `\input` files needs the whole submitted tree,
  not one file:
  `mkdir <scratch dir>/old && git archive <submitted commit> | tar -x -C <scratch dir>/old`, then
  `latexdiff --flatten <scratch dir>/old/<main>.tex <main>.tex > <scratch dir>/diff.tex`. If the
  build fails inside an equation or a table, rerun `latexdiff` with `--math-markup=whole`, or
  with `--exclude-textcmd="section,subsection"` for an error in a heading; if it still fails,
  report the failing lines.

## Write the reply

Start from the owner's most recent reply letter (each paper's `reply/` folder has one; ask which
to use) and keep its preamble and layout. The owner's format:

- An `article`-class letter with one `\section*{Response to the First Referee}` per referee. The
  letter to the editor is a separate file (`reply_editor.tex`).
- Each comment is reproduced in bold, in full and word for word, in the referee's order: typos
  included, nothing summarized, nothing dropped. The response follows in `{\color{blue} ...}`.
- Remarks meant for coauthors go in TeX comments (`% @All: ...`), never in the visible text.

Each response:

- **Says what was done and where,** in the revised paper's own numbering: section, equation,
  figure, reference, paragraph ("second paragraph of Sec. III B"). Where a number changed between
  versions, give both ("Ref. [55] of the original submission, now Ref. [53]"). Page and line
  numbers only if the journal's format prints them.
- **Quotes exactly** when it quotes added or changed text.
- **Claims only what is in the files.** For every "we have added", "we corrected" and "we now
  discuss", open the revised paper and find it. A `new work` item not yet done is
  `\textbf{[CHECK: awaiting ...]}`, not a promise written as a fact.
- **For `disagree`,** gives the position the owner decided, with its evidence (an equation, a
  figure, a reference): firm and courteous, with no concession the owner did not make and no
  remark about the referee.
- **Adds nothing new:** no result, number or citation that is not in the revised paper or the
  record.

**The letter to the editor:** thanks, a short list of the main changes, and any disagreement the
editor should know about. A request (another referee, an appeal) appears only as the owner
dictates it. The corresponding author signs: keep the signature block of the previous letter, and
ask whether the corresponding author or the affiliation has changed since.

## Recheck after the last edit

Any edit to the paper after a response is written can break that response: numbering shifts and
quoted sentences change. When the paper is final, rebuild it and check every response against the
built PDF: each section, equation, figure and reference number cited; each quotation; each item of
the table answered exactly once. Work from the files, not from your memory of the edits. Where
the venue has subagents, give one the reports, the reply and the revised paper, without your
notes, and have it list every claim it cannot find. Then build the reply and read its PDF. Last,
generate the marked copy again from the submitted version, so that the marked PDF and the clean
PDF show the same text.

## Cover letter for a first submission

Copy the layout of the owner's previous cover letter (`cover/cover.tex` in a past paper; ask
which). Its content comes from the paper's abstract and conclusions, with no claim stronger or
broader than the paper makes. Suggested and excluded referees with their affiliations and email
addresses, statements about prior or concurrent submission, and the choice of journal and article
type come from the owner. Never supply a person's email address or affiliation from memory. As
for the letter to the editor, ask who signs and with which affiliation: the signature block of
the previous letter may be out of date.

## Hand over; send nothing

Do not upload to a submission system, email an editor or a coauthor, or push to Overleaf. Hand
the owner the PDFs (reply, letter to the editor, marked paper, clean paper), the open `[CHECK]`
items, the decisions still needed, and a reminder that the `% src:` comments are still in the
source (rule 1).
