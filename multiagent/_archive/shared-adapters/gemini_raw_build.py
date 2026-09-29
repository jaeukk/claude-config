#!/usr/bin/env python3
"""Read a document into per-section study notes using Gemini through ``agy``.

This is the non-Claude host adapter for the extraction contract at
``20_Notes/_shared/contracts/document-note.md``. The Claude host adapters are the
``book-summarizer`` / ``paper-reviewer`` subagent definitions, which *read* that
contract at run time; ``agy`` has no MCP and runs in a disposable cwd with no
access to the vault, so this driver **inlines** the contract text into the prompt
instead. Same rules, different delivery -- that is the whole point of the split.

What stays here (host-dependent): rendering pages, isolating a cwd, invoking
``agy`` with the permission flags it needs, classifying failures, capturing token
usage. What is deliberately *not* here: Zotero resolution, vault paths, note
frontmatter, wikilinks, and file placement. The conductor does those around this
script, because they need MCP and vault write access that this path cannot have.

Usage
-----
``python3 gemini_raw_build.py <job.json>``

The job file is documented in ``_shared/routing.md`` under ``gemini-raw-build``.

Generalised from ``tasks/landau-raw-ingest/run_sections.py`` (2026-07-31), which
read Landau & Lifshitz §1-10 at a cost of 0%p on the Claude and Codex weekly
windows.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

#: Everything below this marker in the contract file is maintainer documentation
#: and is not sent to the model.
CONTRACT_END = "<!-- END OF CONTRACT -->"

#: The three lines the contract requires at the end of every note. They are
#: evidence for the caller, not note content, so they are parsed out and stripped.
#: Matched per line (with any bold/bullet decoration the model added), and only
#: against the *trailing* block -- see :func:`split_self_report`.
SELF_REPORT = re.compile(
    r"[\s*>\-]*(BOUNDARY|EQUATIONS|ILLEGIBLE)\**\s*:\s*(.*)$")

#: A finished note ends on sentence-terminal punctuation, a closing display-math
#: delimiter, or a list/table row. A generation that stopped early ends mid-word
#: or mid-clause. Length is not a truncation signal; this is -- but it is only
#: consulted when the self-report is absent, since a complete self-report is
#: proof the generation reached the end.
COMPLETE_ENDING = re.compile(r"[.!?:;\]\}\$\)»”\"'*|-]\s*$")

#: Wording that marks a policy decision rather than a mechanical failure. The
#: phrases are first-person declines, not the bare word "copyright" -- a note
#: whose subject matter *is* copyright must not be misread as a refusal, because
#: refusals are deliberately never retried.
REFUSAL = re.compile(
    r"(i cannot\b|i can't\b|i am unable\b|i'm unable\b|not able to provide\b"
    r"|copyrighted (?:material|text|work|book)|cannot reproduce\b)", re.I)

#: Backstop only. Density is a weak signal because ranges overlap: a section's
#: last page is shared with the next one and mostly belongs to it, so chars/page
#: is systematically deflated. Set well below any plausible complete note.
MIN_CHARS_PER_PAGE = 500

MAX_ATTEMPTS = 3

ASSIGNMENT = """\
Read the {n} page images {files} in the current directory: consecutive pages of {document}, \
covering "{label}". Write a dense study note of this passage, following the contract below \
exactly.

Begin directly with the note -- no preamble, no closing question.

---

{contract}"""


def load_contract(path: Path) -> str:
    """Return the instruction text of the extraction contract.

    Parameters
    ----------
    path:
        The contract markdown file.

    Returns
    -------
    str
        Everything above :data:`CONTRACT_END`, stripped. Raises if the marker is
        absent, rather than silently shipping maintainer notes to the model.
    """
    text = path.read_text(encoding="utf-8")
    if CONTRACT_END not in text:
        raise SystemExit(f"contract {path} has no {CONTRACT_END} marker")
    return text.split(CONTRACT_END)[0].strip()


def derive_ranges(sections: list[dict], end_page: int) -> list[dict]:
    """Attach an inclusive page range to each section, with a one-page overlap.

    Each section runs from its own start page through the page on which the
    *next* section starts. Ranges derived from heading positions alone do not
    overlap, and the shared page is then lost from both notes -- it is past the
    first section's heading-derived end, and before the second's start. The
    overlap makes both sections responsible for the shared page; the contract
    tells each to decide per paragraph and report the boundary it used.

    Parameters
    ----------
    sections:
        Section dicts with ``number``, ``title`` and ``start`` (printed page).
    end_page:
        Printed page on which the last section ends.

    Returns
    -------
    list[dict]
        The same dicts with ``first`` and ``last`` added.
    """
    out = []
    for i, sec in enumerate(sections):
        last = sections[i + 1]["start"] if i + 1 < len(sections) else end_page
        out.append({**sec, "first": sec["start"], "last": last})
    return out


def render_pages(pdf: Path, pages_dir: Path, first: int, last: int,
                 offset: int, dpi: int) -> None:
    """Render a printed-page range to PNGs, skipping pages already rendered.

    Parameters
    ----------
    pdf:
        Source PDF.
    pages_dir:
        Destination for ``p<printed>.png``.
    first, last:
        Inclusive printed page range.
    offset:
        ``pdf_page = printed_page + offset``.
    dpi:
        Rendering resolution. 200 was enough for display equations in a
        typeset physics textbook; the OCR text layer was not.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    for printed in range(first, last + 1):
        dest = pages_dir / f"p{printed:02d}.png"
        if dest.exists():
            continue
        pdf_page = printed + offset
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-f", str(pdf_page), "-l", str(pdf_page),
             "-singlefile", str(pdf), str(dest.with_suffix(""))],
            check=True, capture_output=True)


def split_self_report(text: str) -> tuple[str, dict[str, str]]:
    """Separate the contract's closing self-report from the note body.

    Parameters
    ----------
    text:
        The model's full response.

    Only the **trailing** block is taken. Matching report-shaped lines anywhere
    would silently delete content: ``BOUNDARY: u = 0 at x = 0`` is a perfectly
    ordinary line in a note about elasticity, and stripping it would remove a
    boundary condition while looking like housekeeping. Scanning backwards from
    the end also tolerates the code fence or blank lines models like to wrap the
    block in.

    Returns
    -------
    tuple[str, dict[str, str]]
        Note body with the report removed, and the report as a dict. Missing
        keys are reported as ``"(not reported)"`` rather than defaulted away --
        a model that skipped the self-check should be visible, not smoothed over.
    """
    lines = text.rstrip().split("\n")
    found: dict[str, str] = {}
    i = end = len(lines)
    while i > 0:
        stripped = lines[i - 1].strip()
        if not stripped or stripped.startswith("```"):
            i -= 1
            if found:
                end = i  # a fence or blank sitting between the note and an
                         # already-matched report belongs to the report block,
                         # including the fence that *opens* it
            continue
        match = SELF_REPORT.match(lines[i - 1])
        if not match:
            break
        # strip the closing "**" of a bolded label, and any bolding of the value
        found.setdefault(match.group(1).lower(),
                         match.group(2).strip().strip("*").strip())
        i -= 1
        end = i  # committed only on a real report line, so a note without one
                 # never loses its trailing blank lines or fences
    body = "\n".join(lines[:end]).rstrip()
    report = {k: found.get(k, "(not reported)")
              for k in ("boundary", "equations", "illegible")}
    return body, report


def classify(text: str, n_pages: int, reported: bool) -> tuple[str, str]:
    """Return ``(verdict, detail)`` for one section's output.

    Two different things leave a section short and they call for opposite
    responses. **Truncation** is a transient failure: retrying the identical
    request is ordinary reliability engineering. **Refusal** is a policy
    decision; retrying it with the same or a firmer prompt is pressuring a model
    past its guardrail, so refusals are reported and left alone. Classification
    is by content, never by length alone, so the two are never conflated.

    Parameters
    ----------
    text:
        The note body.
    n_pages:
        Pages the section covers.
    reported:
        Whether the contract's full self-report was present. A model cannot emit
        the closing report and *also* have been cut off, so this is direct
        evidence of completion and outranks the punctuation heuristic -- which
        otherwise burns three attempts on a note that legitimately ends on an
        unpunctuated list item.

    Returns
    -------
    tuple[str, str]
        Verdict is ``ok``, ``refusal``, ``truncated`` or ``empty``.
    """
    if not text.strip():
        return "empty", "no output"
    per_page = len(text) / max(n_pages, 1)
    if REFUSAL.search(text[:1500]):
        return "refusal", "model declined on policy grounds"
    if not reported and not COMPLETE_ENDING.search(text.rstrip()[-3:]):
        tail = text.rstrip()[-40:].replace("\n", " ")
        return "truncated", f"ends mid-clause: ...{tail!r}"
    if per_page < MIN_CHARS_PER_PAGE:
        return "truncated", f"far too sparse: {per_page:.0f} chars/page"
    return "ok", f"{per_page:.0f} chars/page"


def run_section(sec: dict, job: dict, contract: str, attempt: int) -> dict:
    """Transcribe one section and return its record.

    The section's pages are copied into a fresh scratch directory which becomes
    ``agy``'s cwd. That isolation is the real safety boundary: headless ``agy``
    cannot read files without ``--dangerously-skip-permissions``, which also
    auto-approves write tools, and ``--sandbox`` is a terminal restriction rather
    than a write barrier. So the cwd must always be a disposable copy -- never
    the Zotero store, the vault, or a repository.

    Parameters
    ----------
    sec:
        Section dict carrying ``number``, ``title``, ``first`` and ``last``.
    job:
        The parsed job file.
    contract:
        Contract instruction text, inlined into the prompt.
    attempt:
        1-based attempt counter, recorded in the ledger.

    Returns
    -------
    dict
        Section metadata, verdict, self-report, wall-clock seconds and token usage.
    """
    pages_dir = Path(job["pages_dir"])
    stage = Path(job["work_dir"]) / f"s{sec['number']:02d}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    names = []
    for printed in range(sec["first"], sec["last"] + 1):
        src = pages_dir / f"p{printed:02d}.png"
        shutil.copy(src, stage / src.name)
        names.append(src.name)

    label = f"§{sec['number']}. {sec['title']}"
    prompt = ASSIGNMENT.format(
        n=len(names), files=", ".join(f"'{x}'" for x in names),
        document=job["document"], label=label, contract=contract)

    argv = ["agy", "--model", job.get("model", "gemini-3.6-flash-low"),
            "--add-dir", ".", "--sandbox", "--dangerously-skip-permissions",
            "--output-format", "json", "--prompt", prompt]

    start = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=stage,
                          timeout=job.get("timeout", 900), check=False,
                          stdin=subprocess.DEVNULL)
    elapsed = time.perf_counter() - start

    rec = {"section": sec["number"], "title": sec["title"],
           "pages": [sec["first"], sec["last"]], "n_pages": len(names),
           "attempt": attempt, "seconds": round(elapsed, 1),
           "exit": proc.returncode, "in": -1, "out": -1, "chars": 0,
           "verdict": "error", "detail": "", "report": {}}

    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        rec["detail"] = f"unparseable envelope: {exc}; stderr={proc.stderr[-300:]}"
        return rec

    if env.get("status") != "SUCCESS":
        rec["detail"] = f"agy status {env.get('status')}"
        return rec

    text = str(env.get("response", ""))
    usage = env.get("usage", {})
    rec["in"] = usage.get("input_tokens", -1)
    rec["out"] = usage.get("output_tokens", -1)

    body, report = split_self_report(text)
    rec["chars"] = len(body)
    rec["report"] = report
    reported = all(v != "(not reported)" for v in report.values())
    rec["verdict"], rec["detail"] = classify(body, len(names), reported)

    # Only an accepted section becomes a note. A refusal or a truncation kept its
    # place in out_dir would be indistinguishable from a good note downstream --
    # and a refusal is often a fluent summary, so it does not even look wrong.
    # Failures are still written, under rejected/, because diagnosing them needs
    # the text: it was the *content* of the refusals that revealed the model was
    # declining before it had read the pages.
    if rec["verdict"] == "ok":
        dest = Path(job["out_dir"]) / f"s{sec['number']:02d}.md"
    else:
        dest = Path(job["out_dir"]) / "rejected" / f"s{sec['number']:02d}.try{attempt}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
    if body.strip():
        dest.write_text(body + "\n", encoding="utf-8")
    rec["path"] = str(dest) if body.strip() else ""
    return rec


def main() -> int:
    """Run every section, retrying mechanical failures only.

    Returns
    -------
    int
        ``0`` only when every section was accepted. A partial run exits non-zero
        so a caller that chains this into an ingest stops instead of importing a
        book with holes in it.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job", type=Path, help="job JSON file")
    args = ap.parse_args()

    job = json.loads(args.job.read_text(encoding="utf-8"))
    contract = load_contract(Path(job["contract"]))
    sections = derive_ranges(job["sections"], job["end_page"])

    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = Path(job["pages_dir"])

    if job.get("pdf"):
        for sec in sections:
            render_pages(Path(job["pdf"]), pages_dir, sec["first"], sec["last"],
                         job.get("page_offset", 0), job.get("dpi", 200))

    ledger = out_dir.parent / "usage.jsonl"
    failed: list[tuple[int, str]] = []
    with ledger.open("w", encoding="utf-8") as fh:
        for sec in sections:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                rec = run_section(sec, job, contract, attempt)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"§{sec['number']:<2} pp{sec['first']}-{sec['last']} "
                      f"try {attempt} {rec['seconds']:6.1f}s in={rec['in']:>7} "
                      f"out={rec['out']:>6} {rec['verdict']:<9} {rec['detail']}",
                      flush=True)
                if rec["verdict"] == "ok":
                    print(f"     eq: {rec['report']['equations']}", flush=True)
                    if rec["report"]["equations"] == "(not reported)":
                        print("     WARNING: accepted without an equation "
                              "self-report -- check this one by hand", flush=True)
                    break
                if rec["verdict"] == "refusal":
                    # By design: a guardrail is not a flaky network call.
                    failed.append((sec["number"], "refusal"))
                    print("     refusal -- not retried by design", flush=True)
                    break
                if attempt == MAX_ATTEMPTS:
                    failed.append((sec["number"], rec["verdict"]))

    if failed:
        print(f"\n{len(failed)} of {len(sections)} section(s) NOT written to "
              f"{out_dir} (drafts under rejected/):")
        for num, why in failed:
            print(f"  §{num}: {why}"
                  + ("  -- a policy decision; a human decides, not a retry"
                     if why == "refusal" else ""))
        return 1

    print(f"\nall {len(sections)} sections accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
