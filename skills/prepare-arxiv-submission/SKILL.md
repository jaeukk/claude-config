---
name: prepare-arxiv-submission
description: Reconcile a published paper or accepted manuscript PDF with its LaTeX source, repair and embed references, convert the project into a clean self-contained arXiv source package, resolve arXiv compiler and font incompatibilities, remove build artifacts, inspect blank pages, and draft metadata such as a character-limited abstract. Use for publication-to-preprint conversion, Overleaf-to-arXiv cleanup, BibTeX-to-thebibliography embedding, or failed arXiv LaTeX submissions.
---

# Prepare an arXiv Submission

Produce an arXiv-ready source package while preserving the published scientific content and minimizing template changes. Treat the published PDF as the content authority and a fresh arXiv-supported build as the technical authority.

## Establish scope

1. Identify the published PDF, source directory, arXiv staging directory, main TeX file, bibliography database, and intended compiler.
2. Use a connected reference manager when available; otherwise accept local files. Do not make a library connector mandatory.
3. Confirm renamed or moved directories from the filesystem instead of assuming an earlier path still exists.
4. Inspect the repository state and source manifest. Preserve unrelated and pre-existing changes.
5. Edit only the requested staging directory unless the user explicitly requests changes to the original manuscript.
6. Keep generated builds outside the source tree, preferably in a new `mktemp -d` directory.

Use `scripts/audit_arxiv_source.sh SOURCE_DIR [MAIN_TEX] [ENGINE]` for a read-only inventory and optional clean build. Read `references/latex-arxiv-notes.md` when diagnosing engine, bibliography, or blank-page failures.

## Reconcile the published paper

1. Extract text from the published PDF with `pdftotext` both normally and with `-layout`.
2. Compare title, authors, affiliations, dates, abstract, headings, prose, captions, appendices, acknowledgments, data statements, DOI, and ordered references.
3. Do not mechanically paste PDF extraction over LaTeX. Reconstruct ligatures, math, accents, hyphenation, and line breaks from context.
4. Preserve citation keys and labels when possible. Make narrow, reviewable edits.
5. Report material differences separately from typography-only differences.

## Reconcile and embed references

1. Extract the published reference list and compare entry order and fields against the BibTeX output, not merely the `.bib` text.
2. Correct missing, extra, duplicated, or materially different entries before embedding.
3. Build in a clean temporary directory with the manuscript's working engine and BibTeX/Biber sequence. Confirm citations resolve.
4. Copy the resolved `.bbl` `thebibliography` environment into the main TeX file at the bibliography location.
5. Remove active `\bibliography{...}` and `\bibliographystyle{...}` commands so arXiv cannot invoke BibTeX again. Do not leave two bibliographies.
6. Build again from a different empty directory without the `.bib`, `.bst`, or preexisting `.bbl` available. Verify the `.fls` recorder file does not list them.
7. Delete the bibliography database and styles from the staging package only after that dependency-free build succeeds and only when the user requested a self-contained package or cleanup.

**biblatex/Biber manuscripts.** Steps 4–6 assume BibTeX output. A Biber `.bbl` holds `\entry` records that biblatex reads at build time: it has no `thebibliography` environment to paste, and arXiv does not run Biber. Do not embed it. Ship the generated `<main>.bbl` beside the main TeX file, confirm in the current arXiv documentation that its biblatex version reads that `.bbl` format version, and run the step-6 build from an empty directory with the `.bbl` present and the `.bib` absent. Convert to an embedded `thebibliography` only if the user asks, and then validate the rendered list against the published one.

## Make the source arXiv-compatible

1. Check current arXiv compiler and metadata support from official documentation because supported engines and TeX versions change.
2. Reproduce the submission with the same supported engine from an empty build directory. A successful LuaLaTeX or Overleaf build does not prove pdfLaTeX compatibility.
3. Search the executed code path for `fontspec`, `\setmainfont`, `\newfontfamily`, engine primitives, shell escape, and obsolete allocation commands.
4. Prefer a minimal conditional fix:
   - Guard optional legacy commands, for example `\ifdefined\reserveinserts ... \fi`.
   - Under pdfLaTeX, select a standard Type 1 font package available in TeX Live.
   - Retain the publisher's OpenType setup only in the XeTeX/LuaTeX branch.
   - Preserve column count and page geometry.
5. Verify actual inputs through the `.fls` file. The string `fontspec` may remain in an unexecuted class branch; it must not be loaded by the arXiv build.
6. Do not replace a publisher class wholesale unless targeted compatibility fixes cannot work.

## Diagnose unwanted first or blank pages

1. Inspect physical pages 1 and 2 with `pdftotext -f 1 -l 2 -layout` and render them when necessary.
2. Look for text emitted before `\maketitle`. A common cause is unsupported trailing author metadata, such as an ORCID optional argument that the class does not consume.
3. Remove or convert only the unsupported syntax. Retain identifiers as TeX comments and enter them in arXiv metadata when appropriate.
4. Rebuild and confirm that the title begins on physical page 1 and the total page count decreases by exactly the unintended pages.

## Clean the staging package

1. Derive required local inputs from the `.fls` file of a successful fresh build.
2. Keep the main TeX file, executed class/style files, figures, required fonts, and ancillary/supporting files.
3. Remove generated files such as `.aux`, `.bbl`, `.blg`, `.fdb_latexmk`, `.fls`, `.log`, `.out`, `.pag`, `.synctex.gz`, and `.toc` from the source directory.
4. Do not remove a PDF merely by extension; figures and supporting information may be required. Remove only a known generated main-document PDF when requested.
5. Treat deletion as destructive: resolve explicit targets, avoid broad globs where practical, and state whether deletion was direct or recoverable.
6. Compile once more from a new temporary directory after cleanup.

## Prepare arXiv metadata

1. Check the current official title/abstract character and character-set rules.
2. Count the exact pasted string, including spaces and LaTeX markup.
3. Keep metadata input ASCII-compatible when required; express math using accepted LaTeX.
4. Preserve problem, method, validation range, principal comparison, scaling law, and design significance when shortening an abstract.
5. If the user requests text only, return a paste-ready abstract and do not edit the TeX source.

## Final acceptance checks

Run all checks from a new empty build directory:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir="$BUILD_DIR" paper.tex
```

Adjust the engine only after confirming arXiv supports it. Then verify:

- The command exits successfully.
- The final log has no undefined citations/references, LaTeX errors, undefined control sequences, fatal errors, or emergency stops.
- The `.fls` file shows no unintended bibliography, font, or generated-file dependency.
- `pdfinfo` reports the expected page count.
- Extracted first-page text begins with the article, not leaked preamble metadata.
- No auxiliary files were created in the submission directory.
- Every local file read by the build is included in the package.

Distinguish nonfatal layout/font warnings from errors, but disclose important publisher-class warnings. Lead the handoff with the completed outcome, files changed or removed, compiler used, page count, and any remaining warnings.
