# Figures, captions and references

The rules cited as "rule N" are the five rules in `SKILL.md`.

## Figures

A figure file in the paper's folder is a copy. Its source is a script and a dataset on the code
side of the project.

- **Trace before touching.** For each figure you add or change, find the script and the data that
  drew it and record them above its `\includegraphics`:
  `% src: code 20_Data/<topic>/<run>/plot_x.py`. If you cannot find the script, say so. Do not
  redraw a figure from its PDF or from numbers read off it.
- **Change a figure in its script,** rerun it, and copy the new file in. Never repair a plot by
  editing the caption to match, by scaling or cropping in LaTeX, or by editing the PDF. When
  figure, caption and text disagree, the record (note, data) decides which is wrong.
- **A figure in a paper makes its script depended-on.** A single-use script that sits unversioned
  beside its data falls under the global research-code rule: it is promoted and committed before
  the paper is submitted. List such scripts for the owner.
- **Draw at final size.** In REVTeX `reprint`, one column is 246 pt (3.40 in, 8.65 cm) and the
  full width is 510 pt (7.06 in, 17.9 cm). Set that size in the script and include the file with
  `width=\columnwidth`, or `width=\textwidth` in a `figure*`, so that text prints at the size the
  script chose (no smaller than about 7 pt). A plot drawn 6.4 in wide and included at
  `\columnwidth` prints its 10 pt labels at about 5 pt.
- **Format.** Vector PDF for plots. Raster only for images and density maps, at 300 dpi or more at
  final size, never upscaled. Fonts embedded: `pdffonts <figure>.pdf` shows `yes` in the `emb`
  column of every row.
- **Consistency.** Panels are labeled (a), (b) in the figure and called the same in caption and
  text. One quantity keeps one symbol, color and line style across the paper's figures. Do not
  restyle a figure the task does not name.
- **Caption.** What is plotted against what, with symbols and units; what each curve and marker
  is; the parameter values that fix the plot, from the script or the note (rule 1). No conclusion
  the figure does not show. A reader should understand the figure without the main text.
- **Check the compiled page, not the source.** Render it,
  `pdftoppm -r 150 -png -f <page> -l <page> build/<main>.pdf <scratch dir>/page`, and look at the
  image: readable at print size, nothing clipped, labels and legend agree with the caption, the
  plot shows what the caption and the text say (trends, order of curves, quoted numbers). Every
  figure is cited in the text, and figures are first cited in numerical order.

## References

A paper's `.bib` holds entries exported from the owner's Zotero library by Better BibTeX. The cite
key is the one Zotero already shows for the item, for example `aieta_aberrationfree_2012`.

- **Never write or complete an entry from memory,** and never cite a key you have not just seen in
  Zotero or in the `.bib`. An entry comes out of Zotero; it gets into Zotero from CrossRef or the
  publisher's record.
- **Never rename a key.** Every `\cite` in the paper, and coauthors' text on Overleaf, depend on
  it. An older paper that uses the shared master `.bib` (journal `@string` macros, hand-made keys)
  keeps that file and its keys: do not migrate it.
- **Do not hand-edit an exported entry.** The next export overwrites the edit. Wrong metadata is
  corrected in Zotero, by the owner or with the `zotero-obsidian-sync` write recipes when the
  owner asks, and the entry is exported again.

### Add a reference

Zotero must be running. `zotero-obsidian-sync` has the user ID and the API details. If the calls
below fail, ask the owner to start Zotero and leave `\textbf{[CHECK: cite <DOI or description>]}`
in the text; do not fall back to writing the entry yourself.

1. **Find the item.**
   `curl -s 'http://localhost:23119/api/users/<userID>/items?q=<DOI>&qmode=everything&itemType=-attachment'`.
   `qmode=everything` is required: the default search does not look at the DOI field. The cite key
   is `data.citationKey`. Without a DOI, search title words the same way and confirm authors, year
   and journal before using the hit.
2. **Not in Zotero.** Add it with the Connector recipe in `zotero-obsidian-sync`, taking the
   fields from CrossRef. The Connector saves into the collection selected in the Zotero app, so
   confirm first that this is the project's collection. For an arXiv preprint, check whether it
   has since been published, and add the published version. Then repeat step 1 to get its key.
3. **Export by key,** and append to the paper's `.bib` only the entries whose keys it lacks:
   `curl -s 'http://localhost:23119/better-bibtex/export/item?translator=bibtex&citationKeys=<key1>,<key2>&useJournalAbbreviation=true'`.
   This call skips an unknown key without an error, so compare the keys returned with the keys
   requested. `useJournalAbbreviation=true` gives abbreviated journal names ("Nano Lett."); look
   at an existing entry and drop the flag if the paper's `.bib` uses full names.

A whole collection is
`http://localhost:23119/better-bibtex/export/collection?/1/<COLLECTION_KEY>.bibtex&useJournalAbbreviation=true`
(`1` is My Library). Use it to start a new paper's `.bib`, never to overwrite an existing one: an
existing `.bib` also holds entries from other collections.

### Cite and check

- A citation supports the sentence it stands on. Open the paper before citing it for a specific
  result (rule 1). Lists of the "see, e.g." kind come from the owner or the `30_Literature/`
  notes, not from recall.
- After the build: no undefined citation in the log, and no work under two keys:
  `grep -a -i -o -E 'doi *= *\{[^}]*\}' <file>.bib | tr 'A-Z' 'a-z' | sort | uniq -d` prints
  nothing. Report a duplicate; which key survives is the owner's choice, since both may be cited.
- Read the rendered reference list once: journal abbreviations, capital letters in names within
  titles, entries still cited as arXiv that now have a journal version. Each fix goes through
  Zotero and a new export.
