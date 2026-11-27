#!/usr/bin/env python3
"""Assemble an md-report page from the template, so report content never re-emits the template.

Usage:
    build.py --out OUT.html --title T --main main.html [--eyebrow E] [--h1 H] [--meta HTML]
             [--js visuals.js ...] [--preamble LatexPreamble.md] [--asset image.png ...]

``main.html`` holds the whole ``<main>…</main>`` element. ``{{files}}`` in it becomes
``<stem>_files``, the folder each ``--asset`` is copied into. ``--js`` files are concatenated
into the page's last script block. ``--preamble`` takes the vault note named by a note's
``preamble:`` frontmatter and uses its ``$…$`` lines. The Plotly script is dropped when nothing
plots. Refuses to write inside the Obsidian vault, whose policy forbids HTML with scripts.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "template.html"
VAULT = Path("~/20_Notes").expanduser()


def tex_in_plain_literals(js: str) -> list[str]:
    """String literals ('…', "…", untagged `…`) that hold TeX, whose backslashes JS would drop.

    TeX means a $ not opening ${…} plus a backslash command of 2+ letters (\\approx, \\to); doubled
    backslashes and String.raw`…` are fine, and so are ordinary escapes such as \\n.
    """
    code = re.sub(r"(^|\s)//[^\n]*", r"\1", js)  # drop line comments (not URLs inside strings)
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    tex = lambda lit: re.search(r"\$(?!\{)", lit) and re.search(r"(?<!\\)\\[A-Za-z]{2,}", lit)
    bad = [lit for line in code.splitlines() for lit in re.findall(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", line) if tex(lit)]
    for m in re.finditer(r"`(?:[^`\\]|\\.)*`", code, re.S):
        if code[max(0, m.start() - 10):m.start()] != "String.raw" and tex(m.group(0)):
            bad.append(m.group(0))
    return bad


def _selftest() -> None:
    """The TeX lint catches the single-backslash bug in every literal form and nothing else."""
    bug = r"$qa\approx2\pi$"
    assert tex_in_plain_literals(f"const a = '{bug}';") and tex_in_plain_literals(f'const a = "{bug}";')
    assert tex_in_plain_literals("const a = `" + bug + "`;")
    assert tex_in_plain_literals("const a = `line one\n" + bug + "\nline two`;")  # multi-line template
    assert not tex_in_plain_literals("const a = String.raw`" + bug + "`;")
    assert not tex_in_plain_literals(r"const a = '$qa\\approx2\\pi$';")  # doubled backslashes are valid JS
    assert not tex_in_plain_literals(r"const a = `cost ${x}\n done`; // see note's $\approx$")
    assert not tex_in_plain_literals(r"const a = `$x$ rises\n then falls`;")  # TeX plus an ordinary \n escape
    print("selftest ok")


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--title", required=True)
    ap.add_argument("--main", required=True, type=Path)
    ap.add_argument("--eyebrow", default="")
    ap.add_argument("--h1")
    ap.add_argument("--meta", default="")
    ap.add_argument("--js", action="append", default=[], type=Path)
    ap.add_argument("--preamble", type=Path)
    ap.add_argument("--asset", action="append", default=[], type=Path)
    a = ap.parse_args()

    out = a.out.expanduser().resolve()
    if VAULT.exists() and out.is_relative_to(VAULT.resolve()):
        sys.exit(f"refusing to write inside the vault ({VAULT}): its policy allows only .md and png/jpg/svg/pdf")
    if out.exists() and '<meta name="generator" content="md-report">' not in out.read_text(encoding="utf-8", errors="replace")[:4000]:
        sys.exit(f"refusing to overwrite {out}: it was not made by md-report")
    body = a.main.read_text(encoding="utf-8")
    if "<main" not in body or "</main>" not in body:
        sys.exit(f"{a.main}: must contain the whole <main>…</main> element")
    ids = re.findall(r'\sid="([^"]+)"', body)
    if dups := sorted({i for i in ids if ids.count(i) > 1}):
        sys.exit(f"{a.main}: duplicate id(s) {', '.join(dups)}")
    js = "\n".join(p.read_text(encoding="utf-8") for p in a.js)
    if "</script" in js:
        sys.exit("visuals JS must not contain </script>")
    if bad := tex_in_plain_literals(js):
        sys.exit(f"visuals: TeX in a plain JS string loses its backslashes; use String.raw`…`: {bad[0][:70]}")

    preamble = ""
    if a.preamble:
        preamble = "\n".join(m.group(1) for m in re.finditer(r"^\$(.*)\$\s*$", a.preamble.read_text(encoding="utf-8"), re.M))
        if "`" in preamble or "${" in preamble:
            sys.exit("preamble contains ` or ${, which would break the JS template literal")

    files = f"{out.stem}_files"
    if a.asset:
        (out.parent / files).mkdir(parents=True, exist_ok=True)
        for f in a.asset:
            shutil.copy2(f, out.parent / files / f.name)
    body = body.replace("{{files}}", files)

    page = TEMPLATE.read_text(encoding="utf-8")
    plots = re.search(r'class="[^"]*\b(chart|plot)\b', body) or re.search(r"\b(Plotly|livePlot)\b", js)
    if not plots:
        page = re.sub(r"<script defer src=\"[^\"]*plotly[^\"]*\"></script><!--PLOTLY-->\n?", "", page)
    for key, val in {
        "{{TITLE}}": html.escape(a.title), "{{EYEBROW}}": html.escape(a.eyebrow),
        "{{H1}}": html.escape(a.h1 or a.title), "{{META}}": a.meta, "{{PREAMBLE}}": preamble,
        "<!--MAIN-->": body.strip(), "/*VISUALS*/": js,
    }.items():
        assert page.count(key) == 1, f"template placeholder {key} missing or repeated"
        page = page.replace(key, val)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"{out} ({len(page) // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
