#!/usr/bin/env python3
"""Extract text-markup annotations (highlights, underlines, etc.) from a PDF.

Designed for the `peer-review` agent: given a manuscript PDF that a reviewer has
annotated, pull every highlighted passage together with its page number, the
nearest section heading (best-effort), the nearest manuscript line number(s), the
reviewer's popup comment (if any), and one line of surrounding context. Text is
reconstructed by intersecting each word's bounding box with the annotation quads
(clean), not by `get_textbox` over the quad rect (noisy when text layers overlap).

Usage:
    python3 extract_pdf_highlights.py <manuscript.pdf> [--json out.json] [--md out.md]
                                      [--commented-only]

`--commented-only` keeps only highlights that carry a typed popup comment.
With no --json/--md it prints the Markdown digest to stdout.

Requires: pymupdf (import fitz).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("ERROR: PyMuPDF not installed. Run: pip install pymupdf  (or uvx --with pymupdf ...)")

# Annotation subtypes that mark up existing text (vs. notes/shapes/ink).
TEXT_MARKUP = {"Highlight", "Underline", "StrikeOut", "Squiggly"}

# A token that is purely digits (manuscript line number in the margin).
PURE_DIGITS = re.compile(r"^\d{1,4}$")

# Section-heading shapes — HIGH PRECISION on purpose. A wrong section misleads the
# authors more than a missing one, so we only accept unambiguous headings: a line that
# is *only* a named section, or a roman-numeral / lettered heading. We deliberately do
# NOT guess numbered headings (e.g. "4.3 Foo") because decimals and figure refs
# ("0.37. The", "14.2 MB") masquerade as them, and we ignore font size (equations in a
# big math font triggered false positives).
NAMED_RE = re.compile(
    r"^(?:Abstract|Introduction|Background|Motivation|Theory|Theoretical\s+\w+|"
    r"Model|Methods?|Materials\s+and\s+Methods|Experimental(?:\s+\w+)?|"
    r"Results(?:\s+and\s+Discussion)?|Discussion|Conclusions?|"
    r"Summary(?:\s+and\s+Conclusions?)?|Acknowledg\w*|References|"
    r"Appendix(?:\s+\w+)?|Sup(?:plementary|porting)\s+\w+)\s*$",
    re.IGNORECASE,
)
ROMAN_RE = re.compile(r"^[IVXLC]{1,5}\.\s+[A-Z]")   # II. METHODS
LETTER_RE = re.compile(r"^[A-Z]\.\s+[A-Z]")          # B. Samples
# Numbered (sub)section labels. Section number 1–12, no leading zero (excludes the
# decimal "0.37. The ..." trap), at least one dot (excludes bare page numbers and the
# "14.2 MB" unit). Either a bare label ("2.1") or label + Title-case word ("4.3 Neural").
NUMSEC_RE = re.compile(r"^(?:[1-9]|1[0-2])(?:\.[1-9]\d?){1,2}(?:\s+[A-Z][a-z]+.*)?$")


def _looks_like_heading(txt):
    """High-precision heading test (regex only, no font heuristics)."""
    words = txt.split()
    if not txt or len(words) > 8:
        return False
    if NAMED_RE.match(txt) and len(words) <= 6:
        return True
    if ROMAN_RE.match(txt) or LETTER_RE.match(txt):
        return True
    if NUMSEC_RE.match(txt):
        return True
    return False


def _page_headings(page):
    """Return [(y_top, heading_text), ...] sorted top-to-bottom for one page."""
    d = page.get_text("dict")
    heads = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            txt = re.sub(r"\s+", " ", "".join(s["text"] for s in line.get("spans", []))).strip()
            if txt and _looks_like_heading(txt):
                heads.append((line["bbox"][1], txt))
    heads.sort(key=lambda h: h[0])
    return heads


def _rects_from_annot(annot):
    """Return the list of fitz.Rect covering an annotation's quad points."""
    quads = annot.vertices or []
    return [fitz.Quad(quads[4 * i : 4 * i + 4]).rect for i in range(len(quads) // 4)]


def _words_in_rects(page, rects, frac=0.5):
    """Words whose bbox overlaps any rect by >= `frac` of the word's area."""
    words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
    chosen = []
    for w in words:
        wr = fitz.Rect(w[:4])
        warea = wr.get_area() or 1.0
        for r in rects:
            inter = wr & r
            if inter.is_valid and inter.get_area() >= frac * warea:
                chosen.append(w)
                break
    chosen.sort(key=lambda w: (round(w[1] / 3.0), w[0]))  # reading order
    return chosen


def _split_line_numbers(words):
    """Separate margin line-number tokens from content tokens.

    A pure-digit token counts as a line number only at the far left/right margin
    of the selected words, so inline numbers stay in the text.
    """
    if not words:
        return "", []
    xs = [w[0] for w in words]
    left_edge, right_edge = min(xs), max(xs)
    content, line_nums = [], []
    for w in words:
        tok = w[4]
        is_margin = (w[0] <= left_edge + 2) or (w[0] >= right_edge - 2)
        if PURE_DIGITS.match(tok) and is_margin:
            line_nums.append(int(tok))
        else:
            content.append(tok)
    text = re.sub(r"\s+", " ", " ".join(content)).strip()
    return text, sorted(set(line_nums))


def _context_lines(page, rects):
    """Full text of the page 'lines' that the annotation overlaps (light context)."""
    d = page.get_text("dict")
    out = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            lr = fitz.Rect(line["bbox"])
            if any((lr & r).is_valid and (lr & r).get_area() > 0 for r in rects):
                txt = re.sub(r"\s+", " ", "".join(s["text"] for s in line["spans"])).strip()
                if txt:
                    out.append(txt)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " ".join(uniq)


def extract(pdf_path, commented_only=False):
    doc = fitz.open(pdf_path)
    items = []
    current_section = ""  # carried across pages
    for pno, page in enumerate(doc, start=1):
        headings = _page_headings(page)
        annot = page.first_annot
        while annot:
            if annot.type[1] in TEXT_MARKUP:
                rects = _rects_from_annot(annot)
                info = annot.info or {}
                comment = (info.get("content") or "").strip()
                if not (commented_only and not comment):
                    words = _words_in_rects(page, rects)
                    text, line_nums = _split_line_numbers(words)
                    # nearest heading at or above the annotation on this page,
                    # else the last heading seen on a previous page
                    ay = min((r.y0 for r in rects), default=0.0)
                    section = current_section
                    for hy, htxt in headings:
                        if hy <= ay + 2:
                            section = htxt
                        else:
                            break
                    items.append(
                        {
                            "page": pno,
                            "section": section,
                            "subtype": annot.type[1],
                            "line_numbers": line_nums,
                            "text": text,
                            "context": _context_lines(page, rects),
                            "comment": comment,
                            "author": (info.get("title") or "").strip(),
                            "color": annot.colors.get("stroke"),
                        }
                    )
            annot = annot.next
        if headings:
            current_section = headings[-1][1]
    doc.close()
    return items


def _anchor(it):
    parts = [f"p.{it['page']}"]
    if it.get("section"):
        parts.append(f"Sec. {it['section']}")
    ln = it["line_numbers"]
    if len(ln) == 1:
        parts.append(f"line {ln[0]}")
    elif ln:
        parts.append(f"lines {ln[0]}–{ln[-1]}")
    return ", ".join(parts)


def to_markdown(items, pdf_path):
    n = len(items)
    with_comment = sum(1 for it in items if it["comment"])
    lines = [
        f"# Highlights extracted from `{pdf_path}`",
        "",
        f"**{n}** marked passages ({with_comment} with a typed comment). "
        "Listed in reading order. `section`/`line` anchors are best-effort.",
        "",
    ]
    for i, it in enumerate(items, 1):
        tag = "" if it["subtype"] == "Highlight" else f" _{it['subtype']}_"
        lines.append(f"### {i}. [{_anchor(it)}]{tag}")
        lines.append(f"- **Marked:** {it['text'] or '(no text recovered)'}")
        if it["comment"]:
            lines.append(f"- **Comment:** {it['comment']}")
        if it["context"] and it["context"] != it["text"]:
            lines.append(f"- **Context:** {it['context']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--commented-only", action="store_true",
                    help="keep only highlights that carry a popup comment")
    args = ap.parse_args()

    items = extract(args.pdf, commented_only=args.commented_only)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(items)} annotations -> {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(to_markdown(items, args.pdf))
        print(f"Wrote digest -> {args.md_out}")
    if not args.json_out and not args.md_out:
        print(to_markdown(items, args.pdf))


if __name__ == "__main__":
    main()
