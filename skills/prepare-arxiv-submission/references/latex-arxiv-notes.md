# LaTeX-to-arXiv diagnostic notes

Read this file when the initial clean build fails, bibliography embedding is uncertain, or the PDF begins with an unintended page.

## Useful inspections

```bash
rg -n '\\bibliography|\\bibliographystyle|\\begin\{thebibliography\}|\\bibitem' paper.tex
rg -n 'fontspec|setmainfont|newfontfamily|ifluatex|ifxetex|ifPDFTeX|reserveinserts' . -g '*.tex' -g '*.cls' -g '*.sty'
pdftotext -f 1 -l 2 -layout build/paper.pdf -
pdfinfo build/paper.pdf
```

Search the final log for:

```text
Citation ... undefined
Reference ... undefined
There were undefined
LaTeX Error
Undefined control sequence
Emergency stop
Fatal error
!  ==> Fatal error
```

## Bibliography invariants

- An embedded bibliography is one complete `thebibliography` environment in the main TeX source.
- Each citation key used by `\cite` must have a matching `\bibitem{key}`.
- The clean validation build must succeed without reading `.bib`, `.bst`, or an external `.bbl`.
- Generate the embedded block from a known-good bibliography run; do not hand-format a long reference list unless necessary.
- Compare the rendered ordered list with the published PDF after embedding.
- biblatex/Biber is the exception: its `.bbl` is data for biblatex, not a `thebibliography` environment. Ship the `.bbl` instead of embedding (SKILL.md, "biblatex/Biber manuscripts").

## Engine and font failures

`fontspec` requires XeTeX or LuaTeX. When arXiv requires pdfLaTeX, isolate it behind an engine conditional and use a TeX Live Type 1 font package in the pdfLaTeX branch. Preserve the template's selected one- or two-column mode.

An undefined `\reserveinserts` in an older publisher class is normally a legacy compatibility issue, not a manuscript-content issue. A narrow guard is safer than redefining allocation behavior:

```latex
\ifdefined\reserveinserts\reserveinserts{28}\fi
```

Test the actual code path. Grepping the class alone can produce false positives because unsupported commands may remain in an unexecuted engine branch.

## Blank first pages

If the first physical page contains only a running head or bracketed URLs, inspect author declarations and other material before `\begin{document}`/`\maketitle`. Some publisher classes accept affiliation indices but not a trailing ORCID argument:

```latex
% Risky for a class that accepts only [affiliations]{name}
\author[1,2]{Name}[https://orcid.org/...]

% Safe fallback; retain metadata without typesetting it
\author[1,2]{Name}% ORCID: https://orcid.org/...
```

Do not remove pages from the compiled PDF as a post-processing step when the source is available. Fix the content emitted by LaTeX so arXiv produces the correct PDF itself.

## Cleanup safeguards

- Build outside the source tree before and after cleanup.
- Use the recorder `.fls` as evidence of required local files.
- Do not classify every PDF as an output artifact.
- Do not delete the reference database until a bibliography-independent clean build succeeds.
- Do not touch auxiliary files in neighboring project directories when the user scoped cleanup to one staging folder.
