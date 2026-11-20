---
name: md-report
description: Turn Markdown notes into a visual HTML report. One note becomes a faithful reading copy; several notes (a list, a folder, a topic) become a synthesized overview. The report leads with visuals that Markdown cannot carry - charts and atlases drawn from tables on the page, interactive slider plots of the notes' own formulas, small live demonstrations of constructions the notes define, labeled schematics and concept maps - plus key results, navigable contents, callouts, rendered math and light and dark themes. Every number, quote, formula, citekey and concept-map edge is checked mechanically against the source notes. Use when the user asks for an HTML version of a .md file, an "HTML report", or a visual report or overview of notes or a folder, or invokes /md-report. Markdown stays the default document format, so do not use this unless HTML is asked for. Reports are written outside the vault and are published as an Artifact only on request.
---

# md-report

This skill builds an HTML report from Markdown notes. The notes stay the source of truth. The report's job is to **show what the Markdown can only describe**, and to say nothing the notes don't support.

- **Reading copy** (one note, or a few the user lists to combine): keep every sentence, equation and caveat, and add visuals around them.
- **Synthesis** (a folder, a topic, "a report about" some notes): condense into a narrative organized around visuals. Every claim names its notes.

Files: `reference.md` (all component syntax; read it when writing a page), `example/` (a page that passes verification), `scripts/build.py` (assembles a page), `scripts/verify.py` (checks it). Leave `template.html` and the scripts unread unless a build or verify failure cannot be resolved from their output.

## Where reports go

Never inside the vault (`~/20_Notes`): its policy allows only `.md` and png/jpg/svg/pdf and forbids scripts, and `build.py` refuses such a path.

- Notes from a vault project folder go to that project's `~/10_Projects/<status>/<project>/10_Reports/`. Folder names can differ (`Multistealthy` is `multi-stealthy`): match by the project's `CLAUDE.md`, or ask. If the folder is in git and not ignored, say so in one line.
- Everything else goes to `"$(dirname "$(readlink -f ~/20_Notes)")/30_Reports/"`.

## Procedure

1. **Read every note once, in full.** For a synthesis, also read the notes the folder keeps linking to for its structure (a hub or phase-diagram note) and pass them to `verify.py` as sources. A reading copy passes only the notes being copied, because `--copy` requires every number of every source on the page. Sources are identified by file stem, and `verify.py` refuses two different notes that share one (daily notes of two projects). Pass copies with distinct names. A concept-map edge then verifies only if one of its two notes links to the other by the name passed: omit an edge whose only wikilink uses the old name and tell the user, rather than editing a copy to match. Find images with `find -L <vault> -name <file>`.
2. **Plan the claims, then the visuals** (*Visual menu*). One visual per claim that benefits from one: a reading copy gets at most about 2 beyond its tables, a synthesis about 4–8. A section that is honestly prose stays prose.
3. **Write `main.html` and `visuals.js` in a scratch directory** (the session scratchpad where the venue has one), following `reference.md` and the rules below.
4. **Build:** `python3 ~/.claude/skills/md-report/scripts/build.py --out <path> --title "…" --eyebrow "…" --meta "…" --main main.html --js visuals.js [--preamble <LatexPreamble.md>] [--asset <img> …]`.
5. **Verify:** `python3 ~/.claude/skills/md-report/scripts/verify.py <page> <scratch dir> [--copy] <every source note>` (`--copy` for a reading copy). It prints page problems (console errors, failed `check()` calls, TeX errors, unrendered TeX, missing images, unrendered plots, overflow, overlapping or clipped labels) and accuracy problems (below, including TeX that JavaScript stripped of its backslashes) as text. Fix each one with a small edit to `main.html` or `visuals.js`, rebuild, and rerun until it reports 0 problems.
6. **Look once:** Read the figure sheet(s) that `verify.py` saves (figures only, desktop, light theme). Fix what is visibly broken in one pass, then rebuild and verify. Take no second look unless a fix changed a visual that misstated its data. Use `--phone-shots` only when the phone audit reports a problem you can't place.
7. **Deliver** the path on a line of its own and open it with `explorer.exe "$(wslpath -w <page>)"`.

## Accuracy rules

The notes passed to `verify.py` are the only source: no background knowledge, no memory. If a fact is not in the notes, it is not in the report. `verify.py` enforces the mechanical part; the rest is on you.

- **Attribute every claim.** Each block that states something carries `data-src` with the stems of the notes it actually rests on (mandatory in a synthesis). The visible chip renders from that attribute. Numbers, quotes, citekeys and formulas are checked against exactly those notes, so a correct number cited to the wrong note fails.
- **Quote the claims that matter.** Key results and callout claims are `<q>` excerpts, verbatim, with `…` for cuts. Elsewhere, paraphrase only to shorten: never to strengthen, generalize or merge claims from different notes into one.
- **Copy numbers exactly**, with units, signs and qualifiers ("up to ∼45%", "theoretical, not measured"). Never round, convert or combine them in prose. `verify.py` lets a page drop a minus sign ("a loss of 30 dB" for −30 dB) and cannot tell that from a wrong sign, so check signs yourself. A derived value appears only as a live computation (`class="computed"`), and a value the report chose appears only inside `span.illustrative` (rendered with a visible ✱).
- **Copy formulas, don't retype them.** A formula that states a relation must be the note's TeX verbatim, macros included. The page's own method (a demo's objective, a plotting definition) goes in `<span data-src="editorial">$…$</span>`.
- **Plot only data that sits in a table on the page** (`chartFromTable`, `atlasFromTable`). Never put a data array in JavaScript.
- **Formula plots:** `formula` is the note's TeX, verbatim, written with `String.raw`, and `src` is its note. Define the plotted function once and give it one `check()` that calls it against a printed value, a limit or an identity. The check must fail when the function is mutated. Computed values go in `readout`, never in plot labels.
- **Concept-map edges are wikilinks.** An edge from the report's own grouping is marked `"editorial": true` and the caption says so.
- **Schematics** start their caption with "Schematic." and carry only the notes' numbers. **Editorial framing** is `data-src="editorial"`, with no numbers and no physics claims.
- **Keep caveats with their claims.** Use a note's corrected reading, not its correction log.
- **Report conflicts, don't resolve them.** An inconsistency in the notes (printed values that don't follow the stated formula, say) goes to the user, not into a quiet fix in the page.
- **When unsure, leave it out.**

## Token rules

- To resolve a `verify.py` problem, grep the note for the lines you need instead of rereading it.
- **Don't load** the `dataviz` or `artifact-diagramming` skills (Claude Code only): `reference.md` holds what applies. Where `dataviz` exists, load it only for a chart form `reference.md` doesn't cover.
- Fix with small edits and rebuild; never regenerate a whole file for a small fix.
- **Prefer the data-driven components** (`chartFromTable`, `atlasFromTable`, `conceptMap`, `livePlot`) over custom code. A custom demonstration earns its place only by showing a construction nothing else can, in about 80 lines or fewer.
- **Stay text-first.** `verify.py` output is text and costs little. Read images only as figure sheets, once.

## Visual menu

| The notes contain | Draw |
| --- | --- |
| A numeric table | `chartFromTable` |
| A formula with a parameter the notes vary or bound | `livePlot` with a slider, from that formula |
| A construction or rule (an optimization, a substitution) | A small live demonstration |
| A mechanism, geometry, process, or before/after | A schematic SVG |
| Many studies reporting values of one shared parameter | `atlasFromTable` |
| A classification or two-axis taxonomy | A colored grid (`table.matrix`) |
| Several linked notes | `conceptMap` |
| A headline comparison (A vs B, model vs measurement) | A bar chart from a table (with `±`), or `.cards` |
| A series of figures on shared axes | `div.flip` |

## Reading copy and synthesis

**Reading copy.**
- Keep every sentence, number, equation and caveat, in the note's order. Run `verify.py --copy`; it also lists any source number missing from the page.
- Don't reword. You may split a long paragraph or turn an inline enumeration into a list.
- To combine listed notes: make each one an `h2` part with an `id`. A wikilink from one part to another becomes `a.wikilink` pointing at that `#id`. Name the output `A_and_B.html`.

**Synthesis.**
- Organize by argument, not by note order. Open with the key results and a concept map, then build each section around its planned visuals.
- Where a visual sets numbers from different systems side by side, its caption says so.

## Publishing (only when asked)

The local HTML file is the deliverable on every venue. Publish it only on request, through a
publisher the venue actually has:

- **Claude Code:** load `artifact-design`; copy the page to the scratchpad and remove the
  `<!doctype>`, `<html>`, `<head>` and `<body>` tags; publish it with the Artifact tool, with
  `icon: "report"` and the images passed through `files`.
- **Codex, agy:** there is no Artifact tool. Deliver the verified file and its assets unchanged,
  and publish only through a destination the user names.
