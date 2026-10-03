# Book build brief (role: implementer, one chapter per worker, team account)

American spelling. One-shot worker: there is no user channel, so state every assumption in
your result and in the notes' Issues/Caveats sections; never guess silently. Do not spawn
agents. The vault root, builder id, source PDF, book root, chapter, page range and scope are
supplied under "Per-chapter fields" at the end of this prompt.

## Shell
Bash is allowed only for a named set of commands, matched on the first word: `pdftoppm`,
`pdftotext`, `pdfinfo`, `python3`, `curl`, `wslpath`, `mkdir`, `cp`, `convert`, `magick`,
`identify`, `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `stat`, `file`, `md5sum`,
`date`, `echo`. Call them directly, one command per call, with the full PDF path quoted inline.
A call that starts with a variable assignment or `export`, a `for` loop, `cd` outside the vault,
`bash <script>` or `chmod` is denied without a prompt and only costs a turn. Loops belong in
`python3 -c`; page ranges belong in `pdftoppm -f N -l M`.

## Authority (read first, follow verbatim)
1. `.claude/agents/book-summarizer.md` — you ARE this agent for this run (host workflow,
   output structure, gates).
2. `_shared/contracts/document-note.md` — the extraction contract. The rendered page is the
   transcription source for every equation, figure and table; never the text layer. If you
   cannot read this file, stop and say so.
3. `00_Index/Wiki_Schema.md`, `00_Index/Math_Notation.md`, and the note template under
   `90_Templates/` — match them exactly. Keep the source's own symbols and `\tag{}` its own
   equation numbers.

## Provenance
The frontmatter `agent:` line is `<BUILDER_ID> via book-summarizer v1.4 (claude), <today>`.
Copy `BUILDER_ID` verbatim from the per-chapter fields; keep your §8 `BUILDER:` line consistent.

## Write scope
Only this chapter's folder under the book root, and the book's `_assets/` for figure crops.
Prohibited: other chapters' notes, the book README, `20_Topics/`, `00_Index/`, monthly notes,
`Wiki_Log`, Zotero writes, any policy or config file. A cross-chapter `[[wikilink]]` is
allowed only to a basename that already exists in the book root (build the allow-list with
Glob); an absent target stays plain text.

## Gates and self-report
Run the reconciliation (agent step 11) and the gates the agent names in step 12
(`verify_rebuild.py`, `verify_extraction.py`, `check_wikilinks.py`). Report every verdict as
returned; INDETERMINATE is a result, not a failure to hide. Strip each note's contract §8
self-report block from the published file as the agent directs, and return the blocks in your
result instead.

## Result (final message)
Every path written with its byte count; equations tagged per note; figures and tables carried
(`FIGURES:` / `TABLES:` lines); gate verdicts; the §8 self-report per note; Issues/Caveats.
