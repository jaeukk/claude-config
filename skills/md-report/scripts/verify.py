#!/usr/bin/env python3
"""Verify a built md-report page against its source notes.

Usage:
    verify.py <report.html> <outdir> [--copy] [--phone-shots] <source.md> ...
    verify.py --selftest

Runs the page in headless Chrome at desktop and phone width with the template's audit mode
(``#audit``) and prints, as text:

* page problems: console errors and failed ``check()`` calls, TeX errors, TeX that never rendered,
  missing images, plots that did not render, horizontal overflow, overlapping or clipped labels in
  ``svg.schematic`` figures;
* accuracy problems against the source notes, using the rendered page and the audit's export of
  every formula's original TeX, every quote (formulas restored to TeX) and author-written Plotly
  text:
  - numbers in prose, formulas and plot labels must occur in the notes named by the nearest
    ``data-src`` (or in any source with ``--copy``, which also lists source numbers missing from the
    page). Values inside ``span.illustrative`` (rendered with a visible ✱ mark) are exempt;
    ``data-src="editorial"`` allows no numbers in prose;
  - a formula that states a relation (=, <, \\le, \\sim, \\propto, …) must occur in the math of a
    scoped note (spacing ignored); formulas in an editorial scope are exempt and visibly labeled;
  - ``q`` and ``blockquote`` must be verbatim excerpts of a scoped note ("…" separates excerpts that
    must occur in order; case-sensitive except an excerpt's first letter);
  - concept-map edges need a wikilink between their notes; citekeys and ``data-src`` names must
    exist; in a synthesis every text block needs a ``data-src`` scope.

It saves figure-only contact sheets (desktop, light theme) for one visual look, and phone sheets
with ``--phone-shots``. Exit status 1 if any problem is found, 2 if Chrome cannot run.
"""
from __future__ import annotations

import glob
import html as htmllib
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------------- numbers --
# A number: not glued to an ASCII letter, digit or dot on its left (an underscore is fine, as in
# citekeys like kim_2025). Units may follow ("999nm" -> 999), but not "_" (20_Topics is a name). A minus before it makes it
# negative unless that minus is a subtraction (preceded by a letter, digit or closing bracket).
NUM = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\d_])")
LATEX_CMD = re.compile(r"(\\[A-Za-z]+)(?=\d)")  # \leq0.52 -> \leq 0.52
SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
CITEKEY = re.compile(r"\b[A-Za-z][A-Za-z-]*_[A-Za-z][A-Za-z-]*_(?:19|20)\d\d\b")
RELATION = re.compile(r"[=<>]|\\(?:le|leq|ge|geq|lt|gt|sim|simeq|approx|propto|ll|gg|lesssim|gtrsim|to|equiv|neq|ne|mapsto|Rightarrow|rightarrow)(?![A-Za-z])")


def numbers(text: str) -> set[str]:
    """Return the signed number tokens of `text` (10⁹ reads as 10^9; −3 as -3; 999nm as 999)."""
    t = LATEX_CMD.sub(r"\1 ", text).replace("−", "-")
    t = re.sub("[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+", lambda m: "^" + m.group(0).translate(SUPERSCRIPT), t)
    out = set()
    for m in NUM.finditer(t):
        i, n = m.start(), m.group(0)
        if i > 0 and t[i - 1] == "-" and (i == 1 or not (t[i - 2].isalnum() or t[i - 2] in ").]}")):
            n = "-" + n
        out.add(n)
    return out


TEXT_GROUPS = re.compile(r"\\(?:text|textrm|textit|textbf|mathrm|operatorname|mbox|mathit)\s*\{[^{}]*\}|\\rm\s*[A-Za-z]+")


def lost_backslashes(tex: str, cmds: set[str]) -> str | None:
    """Return why `tex` looks like TeX whose backslashes JavaScript dropped, or None.

    `cmds` are the command names (2+ letters) written in the page source, where any stripped command
    came from. A stripped command leaves its name at the end of a bare letter run ('x\\le y' becomes
    'xle y'); '\\nu' and '\\to' leave a newline or tab before the rest; '\\beta', '\\varepsilon',
    '\\frac' and '\\rho' leave control characters.
    """
    if re.search(r"[\x08\x0b\x0c\r]", tex):
        return "a control character (\\b, \\v, \\f or \\r from \\beta, \\varepsilon, \\frac, \\rho)"
    t = TEXT_GROUPS.sub(" ", tex)
    alt = lambda names: "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
    if cmds and (m := re.search(r"(?<![\\A-Za-z])[A-Za-z]*?(" + alt(cmds) + r")(?![A-Za-z])", t)):
        return f"'{m.group(1)}' without its backslash"
    for esc, ch in (("n", "\n"), ("t", "\t")):
        rest = {c[1:] for c in cmds if c[0] == esc}
        if rest and (m := re.search(ch + "(" + alt(rest) + r")(?![A-Za-z])", t)):
            return f"a {'newline' if esc == 'n' else 'tab'} where \\{esc}{m.group(1)} was"
    return None


def note_numbers(md: str) -> set[str]:
    """Numbers of a note, plus the unsigned form of each negative (a page may drop a sign, never add one)."""
    ns = numbers(md)
    return ns | {n[1:] for n in ns if n.startswith("-")}


# ------------------------------------------------------------------------------ mini DOM --
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    """An element: tag, attributes, children (Nodes or strings) and parent."""

    def __init__(self, tag: str, attrs: dict, parent: "Node | None") -> None:
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []

    def cls(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def closest(self, pred) -> "Node | None":
        n = self
        while n is not None:
            if pred(n):
                return n
            n = n.parent
        return None


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#root", {}, None)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str) -> Node:
    """Parse HTML into a mini DOM; return its root."""
    t = _Tree()
    t.feed(html)
    return t.root


SKIP_TAGS = {"script", "style", "head", "nav", "noscript", "mjx-container"}  # math is checked from its TeX
SKIP_CLASSES = {"doc-head", "controls", "computed", "src", "illustrative", "illustrative-note", "js-plotly-plot"}


def text_runs(root: Node):
    """Yield (string, parent) for visible prose, skipping chrome, controls, math and computed output."""
    def walk(n: Node):
        if n.tag in SKIP_TAGS or n.cls() & SKIP_CLASSES:
            return
        for c in n.children:
            if isinstance(c, str):
                if c.strip():
                    yield c, n
            else:
                yield from walk(c)
    yield from walk(root)


def scope_of(n: Node) -> list[str] | None:
    """Names in the nearest data-src (None if unscoped)."""
    src = n.closest(lambda m: "data-src" in m.attrs)
    return src.attrs["data-src"].split() if src else None


# ---------------------------------------------------------------------------- normalizing --
def norm_text(s: str) -> str:
    """Normalize Markdown or page text for verbatim quote matching (case is kept)."""
    s = re.sub(r"\[\[([^\]|#]*)(?:#[^\]|]*)?\|([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"\[\[(?:[^\]|#]*/)?([^\]|#]*)(?:#[^\]]*)?\]\]", r"\1", s)
    s = re.sub(r"^\s*>\s?(\[![A-Za-z]+\][-+]?)?", "", s, flags=re.M)
    s = s.replace("**", "").replace("*", "").replace("`", "")
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", " ", s).strip()


def norm_tex(s: str) -> str:
    """Normalize TeX: drop callout '>' prefixes, spacing commands, sizing and whitespace."""
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)\b|\\[,;:! ]|\\q?quad\b", "", s)
    return re.sub(r"\s+", "", s)


def note_math(md: str) -> str:
    """All math regions of a note ($$…$$, $…$, \\begin{…}…\\end{…}), normalized and joined by §."""
    body = re.sub(r"^\s*>\s?", "", md, flags=re.M)
    regions = re.findall(r"\$\$(.+?)\$\$", body, re.S)
    rest = re.sub(r"\$\$.+?\$\$", " ", body, flags=re.S)
    regions += re.findall(r"\$([^$\n]+?)\$", rest)
    regions += [m.group(0) for m in re.finditer(r"\\begin\{([A-Za-z*]+)\}.+?\\end\{\1\}", body, re.S)]
    return "§".join(norm_tex(r) for r in regions)


def links(md: str, target: str) -> bool:
    """True if Markdown `md` has a wikilink to note `target` ([[target]], [[x/target|a]], [[target#h]])."""
    return re.search(r"\[\[(?:[^\]|#]*/)?" + re.escape(target) + r"(?:[\]|#])", md) is not None


def excerpt_in(md_norm: str, quote: str) -> bool:
    """True if the excerpts of `quote` (split at …) occur in order in normalized note text."""
    pos = 0
    for part in (norm_text(p) for p in quote.split("…")):
        if not part:
            continue
        i = md_norm.find(part, pos)
        if i < 0 and part[0].isalpha():  # an excerpt may start a sentence the note continues, or vice versa
            i = md_norm.find(part[0].swapcase() + part[1:], pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


# --------------------------------------------------------------------------- accuracy check --
def check_accuracy(src_html: str, dom_html: str, audit: dict, sources: dict[str, str], copy: bool) -> list[str]:
    """Return accuracy problems of a page (source HTML, rendered DOM, audit export) against its notes."""
    probs: list[str] = []
    nums = {k: note_numbers(v) for k, v in sources.items()}
    maths = {k: note_math(v) for k, v in sources.items()}
    norms = {k: norm_text(v) for k, v in sources.items()}
    dom, src = parse(dom_html), parse(src_html)

    def resolve(names: list[str] | None, what: str) -> list[str] | None:
        """Scoped note names (all sources when unscoped in copy mode); None means unscoped."""
        if names is None:
            return list(sources) if copy else None
        bad = [s for s in names if s not in sources and s != "editorial"]
        if bad:
            probs.append(f"SOURCE   unknown note {', '.join(bad)} in data-src ({what})")
        return [s for s in names if s in sources or s == "editorial"]

    def check_numbers(found: set[str], names: list[str] | None, what: str) -> None:
        cands = resolve(names, what)
        if not found:
            return
        if cands is None:
            probs.append(f"UNSOURCED number(s) {', '.join(sorted(found))} with no data-src: “{what}”")
            return
        pool = set().union(*(nums[c] for c in cands if c != "editorial")) if cands else set()
        for n in sorted(found - pool):
            probs.append(f"NUMBER   {n} not in [{', '.join(cands) or '—'}]: “{what}”")

    def check_citekeys(text: str, names: list[str] | None, what: str) -> None:
        cands = [c for c in (resolve(names, what) or []) if c != "editorial"]
        for key in sorted(set(CITEKEY.findall(text))):
            if cands and not any(key in sources[c] for c in cands):
                probs.append(f"CITEKEY  {key} not in [{', '.join(cands)}]: “{what}”")

    # prose: numbers and citekeys in visible text, scoped by data-src
    for s, parent in text_runs(dom):
        ctx = re.sub(r"\s+", " ", s.strip())[:70]
        names = scope_of(parent)
        check_numbers(numbers(s), names, ctx)
        check_citekeys(s, names, ctx)

    # in a synthesis every text block needs a scope
    if not copy:
        for el in dom.iter():
            if el.tag in ("p", "li", "figcaption", "td", "dd", "dt", "blockquote") and el.closest(lambda m: m.tag == "main"):
                if el.closest(lambda m: m.tag in SKIP_TAGS or bool(m.cls() & SKIP_CLASSES)):
                    continue
                if re.sub(r"\W+", "", "".join(t for t, _ in text_runs(el))) and scope_of(el) is None:
                    probs.append(f"UNSOURCED block <{el.tag}> has no data-src: “{el.text().strip()[:60]}”")

    # TeX found verbatim in the page source or a note never passed through JS, so it kept its backslashes
    # (a match right after a letter or backslash is a stripped command: "u_c" inside "\nu_c")
    written = norm_tex(htmllib.unescape(src_html)) + "§" + "§".join(maths.values())
    cmds = set(re.findall(r"\\([A-Za-z]{2,})", htmllib.unescape(src_html)))
    handwritten = lambda t: bool(t) and re.search(("(?<![\\\\A-Za-z])" if t[0].isalpha() else "") + re.escape(t), written) is not None

    # formulas: TeX that lost its backslashes (any scope), numbers, and relations must be the notes' own math
    for m in audit.get("math", []):
        if (why := lost_backslashes(m["tex"], cmds)) and not handwritten(norm_tex(m["tex"])):
            probs.append(f"TEXLOST  ${m['tex'][:60]}$ has {why}: write TeX in JavaScript with String.raw")
        if m.get("illustrative"):
            continue
        names = m["src"].split() if m.get("src") else None
        what = "$" + m["tex"][:60] + "$"
        if names == ["editorial"]:
            continue
        if names is None and not copy:  # a bare symbol (a heading's $S(k)$) has nothing to check
            if RELATION.search(m["tex"]) or numbers(m["tex"]):
                probs.append(f"UNSOURCED formula with no data-src: {what}")
            continue
        check_numbers(numbers(m["tex"]), names, what)
        cands = [c for c in (resolve(names, what) or []) if c != "editorial"]
        if RELATION.search(m["tex"]) and cands and not any(norm_tex(m["tex"]) in maths[c] for c in cands):
            probs.append(f"FORMULA  not in the math of [{', '.join(cands)}]: {what}")

    # quotes, with formulas restored to TeX
    for q in audit.get("quotes", []):
        names = q["src"].split() if q.get("src") else None
        cands = [c for c in (resolve(names, "quote") or list(sources)) if c != "editorial"]
        if norm_text(q["text"]) and not any(excerpt_in(norms[c], q["text"]) for c in cands):
            probs.append(f"QUOTE    not verbatim in [{', '.join(cands) or '—'}]: “{q['text'].strip()[:80]}”")

    # author-written Plotly text (names, annotations, hover templates, axis titles)
    for pl in audit.get("plots", []):
        names = pl["src"].split() if pl.get("src") else None
        for t in pl.get("texts", []):
            check_numbers(numbers(t), names if names != ["editorial"] else ["editorial"], "plot label: " + t[:60])
            check_citekeys(t, names, "plot label: " + t[:60])

    # concept-map edges
    for sc in src.iter():
        if sc.tag == "script" and "concept-map" in sc.cls():
            spec = json.loads(sc.text())
            groups = {g["title"]: g["nodes"] for col in spec["columns"] for g in col if g.get("title")}
            resolve([s for col in spec["columns"] for g in col for s in g["nodes"]], "concept map")
            for e in spec.get("edges", []):
                if e.get("editorial"):
                    continue
                for a in groups.get(e["from"], [e["from"]]):
                    for b in groups.get(e["to"], [e["to"]]):
                        if a in sources and b in sources and not (links(sources[a], b) or links(sources[b], a)):
                            probs.append(f"EDGE     {a} → {b} ({e.get('label', '')}): no wikilink between the notes")

    if copy:  # every number of the notes must survive in what the reader sees
        page: set[str] = set()
        for s, _ in text_runs(dom):
            page |= numbers(s)
        for m in audit.get("math", []):
            page |= numbers(m["tex"])
        page |= {n[1:] for n in page if n.startswith("-")}
        for name, md in sources.items():
            for n in sorted(numbers(md) - page):
                probs.append(f"MISSING  {n} from {name} is not on the page")
    return list(dict.fromkeys(probs))


# ------------------------------------------------------------------------------ page audit --
def chrome_bin() -> str:
    """Path of a headless Chrome, or exit with an install hint."""
    found = sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
    exe = found[-1] if found else shutil.which("chromium") or shutil.which("google-chrome")
    if not exe:
        sys.exit("no headless Chrome; install one with: npx playwright install chromium-headless-shell")
    return exe


def run_page(url: str, width: int, height: int, dark: bool, shot: Path | None = None) -> tuple[str, list[str]]:
    """Load `url` (audit mode); return (dumped DOM, console messages), or save a screenshot."""
    args = [chrome_bin(), "--headless", "--hide-scrollbars", "--virtual-time-budget=15000",
            f"--window-size={width},{height}", "--enable-logging=stderr", "--v=0"]
    if dark:
        args.append("--blink-settings=preferredColorScheme=0")  # --force-dark-mode is ignored here
    args += [f"--screenshot={shot}", url] if shot else ["--dump-dom", url + "#audit"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or (shot is not None and not shot.exists()) or (shot is None and not r.stdout.strip()):
        tail = "\n".join(r.stderr.strip().splitlines()[-5:])
        print(f"CHROME   failed (exit {r.returncode}); last stderr lines:\n{tail}", file=sys.stderr)
        sys.exit(2)
    msgs = re.findall(r':CONSOLE[^\]]*\] "(.*)", source: ', r.stderr)
    return r.stdout, msgs


def audit_of(dom: str) -> dict:
    """The JSON the page's audit mode wrote into <body data-audit>."""
    m = re.search(r'<body[^>]*\sdata-audit="([^"]*)"', dom)
    if not m:
        return {"problems": ["audit did not run (no data-audit on <body>)"], "figures": [], "height": 4000}
    return json.loads(htmllib.unescape(m.group(1)))


def figure_sheets(png: Path, figures: list[dict], out: Path, stem: str, scale: float) -> list[Path]:
    """Crop every figure from a full-page screenshot and pack them into ≤2000 px sheets, 2 columns."""
    from PIL import Image, ImageDraw
    im = Image.open(png).convert("RGB")
    crops = []
    for i, f in enumerate(figures, 1):
        box = (max(0, int(f["left"]) - 6), max(0, int(f["top"]) - 6), min(im.width, int(f["right"]) + 6), min(im.height, int(f["bottom"]) + 6))
        if box[3] - box[1] < 10:
            continue
        c = im.crop(box)
        c = c.resize((max(1, int(c.width * scale)), max(1, int(c.height * scale))))
        ImageDraw.Draw(c).text((4, 2), f"#{i}", fill=(200, 0, 0))
        crops.append(c)
    sheets, paths = [], []
    col_w = max((c.width for c in crops), default=1)
    cur, heights = [[], []], [0, 0]
    for c in crops:
        k = 0 if heights[0] <= heights[1] else 1
        if heights[k] + c.height > 2000 and (cur[0] or cur[1]):
            sheets.append(cur)
            cur, heights, k = [[], []], [0, 0], 0
        cur[k].append(c)
        heights[k] += c.height + 12
    if cur[0] or cur[1]:
        sheets.append(cur)
    for n, cols in enumerate(sheets, 1):
        h = max(sum(c.height + 12 for c in col) for col in cols)
        sheet = Image.new("RGB", (2 * col_w + 12, min(2000, h)), (128, 128, 128))
        for k, col in enumerate(cols):
            y = 0
            for c in col:
                sheet.paste(c, (k * (col_w + 12), y))
                y += c.height + 12
        p = out / f"{stem}-figures-{n:02d}.png"
        sheet.save(p)
        paths.append(p)
    return paths


def main(argv: list[str]) -> int:
    if argv == ["--selftest"]:
        _selftest()
        return 0
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    page, out, srcs = Path(args[0]).resolve(), Path(args[1]), [Path(a) for a in args[2:]]
    out.mkdir(parents=True, exist_ok=True)
    stem, url = page.stem, page.as_uri()
    problems: list[str] = []

    dom, msgs = run_page(url, 1280, 2000, dark=False)
    pdom, pmsgs = run_page(url, 400, 2000, dark=True)
    (out / f"{stem}.dom.html").write_text(dom, encoding="utf-8")
    a, pa = audit_of(dom), audit_of(pdom)
    problems += [f"CONSOLE  {m}" for m in sorted(set(msgs + pmsgs))]
    problems += [f"PAGE     desktop: {p}" for p in a["problems"]] + [f"PAGE     phone: {p}" for p in pa["problems"]]
    if srcs:
        sources = {p.stem: p.read_text(encoding="utf-8") for p in srcs}
        problems += check_accuracy(page.read_text(encoding="utf-8"), dom, a, sources, copy="--copy" in flags)

    shots = []
    full = out / f"{stem}-desktop.png"
    run_page(url, 1280, min(int(a.get("height", 4000)) + 100, 30000), dark=False, shot=full)
    shots += figure_sheets(full, a["figures"], out, stem, 0.8)
    if "--phone-shots" in flags:
        pfull = out / f"{stem}-phone.png"
        run_page(url, 400, min(int(pa.get("height", 4000)) + 100, 30000), dark=True, shot=pfull)
        shots += figure_sheets(pfull, pa["figures"], out, stem + "-phone", 1.0)

    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s); {len(a['figures'])} figures; sheets:")
    for s in shots:
        print(f"  {s}")
    return 1 if problems else 0


# ------------------------------------------------------------------------------- selftest --
def _selftest() -> None:
    """Assert that each accuracy rule catches its error and passes its correct case."""
    A = ("---\nup: \"[[B]]\"\n---\nChi is $0.3\\leq\\chi\\leq0.52$ in [[kim_ultra_2025]]; **sharp** onset at 0.35.\n"
         "$$\\xi\\sim\\ell_t\\,e^{\\pi^2\\ell_t/\\lambda_e}$$\nThe window widens with stealthiness. Power is 3 mW.\n"
         "The invalid model y=x is rejected; use $y=-x$. A loss of $-30$ dB. Holds for $2k\\le K$ only.\n")
    srcs = {"A": A, "B": "Gap at 0.45. See [[A|the first note]].\n", "C": "Unrelated note, 99.\n",
            "D": "> [!formula] x\n> $$\n> \\frac{\\left\\langle P_a\\right\\rangle}{P_0}\n> \\simeq 1-\\exp\\!\\left(-\\frac{L}{\\ell_a^B}\\right).\n> $$\n",
            "E": "$$\nu(x)=\\sigma_{abs}\n$$\n"}

    def run(body: str, copy: bool = False, plots: list | None = None, js: str | None = None) -> list[str]:
        """Check `body` as if rendered: $…$ become typeset math items, q/blockquote become quotes.

        With `js`, the page source is that script alone, as if it had written `body`.
        """
        root, math = parse(body), []
        for n in root.iter():
            for c in n.children:
                if isinstance(c, str):
                    for m in re.finditer(r"\$([^$]+)\$", c):
                        s = scope_of(n)
                        math.append({"tex": m.group(1), "src": " ".join(s) if s else None,
                                     "illustrative": bool(n.closest(lambda x: "illustrative" in x.cls()))})
        quotes = [{"text": q.text(), "src": " ".join(scope_of(q) or []) or None} for q in root.iter() if q.tag in ("q", "blockquote")]
        dom = re.sub(r"\$[^$]+\$", "<mjx-container></mjx-container>", body)
        page = f"<html><body><main>{dom}</main></body></html>"
        return check_accuracy(f"<main></main><script>{js}</script>" if js else f"<main>{body}</main>", page, {"math": math, "quotes": quotes, "plots": plots or []}, srcs, copy)

    has = lambda probs, kind: any(p.startswith(kind) for p in probs)
    # numbers: scoped, unsourced, editorial, illustrative, unknown source
    assert run('<p data-src="A">from 0.3 to 0.52, onset 0.35</p>') == []
    assert has(run('<p data-src="A">gap at 0.45</p>'), "NUMBER   0.45")       # B's number cited to A
    assert run('<p data-src="B">gap at 0.45</p>') == []
    assert has(run("<p>gap at 0.45</p>"), "UNSOURCED")
    assert has(run('<p data-src="editorial">grouped 3 ways</p>'), "NUMBER")
    assert run('<p data-src="A">N = <span class="illustrative">999</span> points</p>') == []
    assert has(run('<p data-src="A">The measured gain is 999.</p>'), "NUMBER   999")
    assert has(run('<p data-src="Nope">x</p>'), "SOURCE")
    assert has(run("<p>A claim with no numbers and no source.</p>"), "UNSOURCED block")
    assert not any("UNSOURCED" in p for p in run("<p>A claim.</p>", copy=True))
    # tokenization: units, superscripts, signs
    assert numbers("999nm") == {"999"} and numbers("10⁹") == {"10", "9"} and numbers("−3") == {"-3"}
    assert numbers("20_Topics/40_Runs, kim_ultra_2025") == {"2025"}
    assert numbers("x−3") == {"3"} and numbers(r"$0.3\leq\chi\leq0.52$, [[Leseur_2016]]") == {"0.3", "0.52", "2016"}
    assert has(run('<p data-src="A">a gain of −3</p>'), "NUMBER   -3")         # A has 3 but not −3
    assert run('<p data-src="A">a loss of 30 dB</p>') == []                     # A has −30; dropping a sign is allowed
    assert has(run('<p data-src="A">a width of 999nm</p>'), "NUMBER   999")
    assert has(run('<p data-src="A">up to 10⁹</p>'), "NUMBER   9")
    # quotes: verbatim, ordered excerpts, case-sensitive, math restored
    assert run('<p data-src="A"><q>sharp onset at 0.35</q></p>') == []
    assert run('<p data-src="A"><q>Chi is … onset at 0.35</q></p>') == []
    assert has(run('<p data-src="A"><q>onset at 0.35 … Chi is</q></p>'), "QUOTE")
    assert has(run('<p data-src="A"><q>The window narrows with stealthiness</q></p>'), "QUOTE")
    assert has(run('<p data-src="A"><q>Power is 3 MW.</q></p>'), "QUOTE")
    assert run('<p data-src="A"><q>power is 3 mW.</q></p>') == []
    assert run('<p data-src="A"><q>Holds for $2k\\le K$ only.</q></p>') == []
    # formulas: relations must be the notes' math; regions only; editorial exempt
    assert run('<p data-src="A">$\\xi\\sim\\ell_t e^{\\pi^2 \\ell_t/\\lambda_e}$</p>') == []
    assert has(run('<p data-src="A">$\\xi\\sim\\ell_t e^{\\pi \\ell_t}$</p>'), "FORMULA")
    assert has(run('<p data-src="A">$y=x$</p>'), "FORMULA")                      # only in prose, not in A's math
    assert has(run("<p>$y=-x$</p>"), "UNSOURCED formula")                        # an equation-only block without a scope
    assert run("<h2>$S(k)$ as a knob</h2>") == []                                 # a bare symbol needs no scope
    assert run('<p data-src="A">$\\ell_t$ and $\\chi$</p>') == []
    assert run('<p data-src="A">intensity <span data-src="editorial">$|x|^2/N=1$</span></p>') == []
    assert run('<p data-src="D">$\\frac{\\langle P_a\\rangle}{P_0}\\simeq 1-\\exp(-\\frac{L}{\\ell_a^B})$</p>') == []
    # TeX that JavaScript stripped of its backslashes, however the string was written
    T = lambda tex, js: has(run(f'<p data-src="A">${tex}$</p>', js=js), "TEXLOST")
    assert T("xapprox y", r"`${`$x\approx y$`}`")                         # the nested template
    assert T("xle y", r"`${`$x\le y$`}`") and T("psi", r"'$\psi$'")       # glued short command; any command
    assert T("qaapprox2pi", r"'$qa\approx2\pi$'") and T("lambda_e", r"'$\lambda_e$'")  # A's \lambda_e: no exemption
    assert T("S(q\to0)", r"'$S(q\to0)$'") and T("\nu_c", r"'$\nu_c$'")    # tab + 'o', newline + 'u'
    assert T("\x0crac{1}{2}", r"'$\frac{1}{2}$'")                         # form feed
    # TeX written in the page or found in a note is exempt, so verbatim H_{int} or a line starting with u passes
    assert not has(run(r'<p data-src="A">$H_{int}$ by $\int f$</p>'), "TEXLOST")
    assert run('<p data-src="E">$\nu(x)=\\sigma_{abs}$</p>', js=r"String.raw`$\abs{x}$, $\nu$`") == []
    for ok in ("a=b\n+c", "x\n\\to y", r"a \text{ to } b", r"\phi_{\rm sigma}", r"\Sigma_{\rm ISA}", r"n_{\text{eff}}", r"\angle x", r"\Delta_{let}",
               r"\left\langle P_a\right\rangle", r"f_c=0.41\,\mathrm{MHz}", r"k_r\ell_s^0", r"\mathrm{Var}[\ln g]", r"\phi_{\rm paper}"):
        assert lost_backslashes(ok, {"to", "text", "rm", "le", "sigma", "angle", "ln", "nu", "approx"}) is None, ok
    # plot labels and citekeys
    assert has(run('<p data-src="A">x</p>', plots=[{"texts": ["Measured gain 999"], "src": "A"}]), "NUMBER   999")
    assert run('<p data-src="A">x</p>', plots=[{"texts": ["onset 0.35"], "src": "A"}]) == []
    assert has(run('<p data-src="B">see kim_ultra_2025</p>'), "CITEKEY")
    assert run('<p data-src="A">see kim_ultra_2025</p>') == []
    # concept-map edges
    good = '{"columns": [[{"title": "", "nodes": ["A"]}], [{"title": "G", "nodes": ["B"]}]], "edges": [{"from": "A", "to": "G"}]}'
    bad = '{"columns": [[{"title": "", "nodes": ["A", "C"]}]], "edges": [{"from": "C", "to": "A"}]}'
    assert run(f'<script type="application/json" class="concept-map">{good}</script>') == []
    assert has(run(f'<script type="application/json" class="concept-map">{bad}</script>'), "EDGE")
    # reading copy: missing numbers are counted from what the reader sees, not from scripts
    assert has(run("<p>0.3 0.52 0.35 0.45 2025</p><script>const n = 99;</script>", copy=True), "MISSING  99")
    print("selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
