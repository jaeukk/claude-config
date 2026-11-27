#!/usr/bin/env python3
"""Mechanical checks for a daily research note.

Usage: check_note.py NOTE.md        (exit 1 on any ERROR)
       check_note.py --selftest

Checks only what a regex can see: filename/date identity, one frontmatter
block, the template's H1 set (each once), one Next Steps, absolute paths,
date display text on intra-project note links, no wikilinks inside
sim-tracker fences, caption directly below its embed, no \\eqno.
"""
import re
import sys
from pathlib import Path

H1_ALLOWED = {"Objectives", "Context", "Plan", "Notes", "Findings", "Next Steps", "References"}
TITLE = re.compile(r"^# (\d{4}-\d{2}-\d{2}) -- Research Note$")
LOCAL_ABS = re.compile(r"(?<![\w/.])(/home/|/mnt/|/media/|[A-Za-z]:\\)")
REMOTE_ABS = re.compile(r"(?<![\w/.])/(scratch|home01|tigress|projects|gpfs)/")
NOTE_LINK = re.compile(
    r"\[\[([^\]|#]*10_ResearchNotes[^\]|#]*?(\d{4}-\d{2}-\d{2}))(\.md)?(#[^\]|]*)?(\\?\|([^\]]*))?\]\]")
EMBED = re.compile(r"^!\[\[[^\]]*\.(png|jpe?g|svg|pdf)(\|[^\]]*)?\]\]\s*$", re.I)
CAPTION = re.compile(r"^\*{1,2}Figs?\.?\s")
INLINE_CODE = re.compile(r"``[^`]*``|`[^`]*`")
KEY_LINE = re.compile(r"^[\w-]+:")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
NEXT_SUB = re.compile(r"^#{2,}\s*next[- ]?steps?\b", re.I)


def frontmatter_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """1-based (open, close) of the top '---' block, plus any later unfenced '---' block
    that carries a created: line (a pasted template head); plain thematic breaks are ignored."""
    blocks, i, fence = [], 0, None
    while i < len(lines):
        s = lines[i].lstrip()
        if (m := FENCE.match(s)):
            if fence is None:
                fence = m.group(1)
            elif m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
        elif fence is None and s.strip() == "---":
            for j in range(i + 1, min(i + 60, len(lines))):
                if FENCE.match(lines[j]):
                    break
                if lines[j].strip() == "---":
                    body = lines[i + 1:j]
                    keys = sum(bool(KEY_LINE.match(l)) for l in body)
                    if (i == 0 and keys) or keys >= 2 or any(l.startswith("created:") for l in body):
                        blocks.append((i + 1, j + 1))
                    i = j
                    break
        i += 1
    return blocks


def check(text: str, filename: str) -> list[tuple[str, int, str]]:
    """Return (level, line_no, message) findings for one note body."""
    out: list[tuple[str, int, str]] = []
    err = lambda n, m: out.append(("ERROR", n, m))
    warn = lambda n, m: out.append(("WARN", n, m))
    date = filename[:-3]
    if not (filename.endswith(".md") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
        err(0, "filename must be YYYY-MM-DD.md")
    lines = text.split("\n")
    blocks = frontmatter_blocks(lines)
    if not blocks or blocks[0][0] != 1:
        err(1, "note must start with a '---' frontmatter block")
    for b in blocks[1:]:
        err(b[0], "second frontmatter block; append under a section instead")
    fm_end = blocks[0][1] if blocks and blocks[0][0] == 1 else 0
    created = [l for l in lines[:fm_end] if l.startswith("created:")]
    if len(created) != 1:
        err(1, f"expected one created: line in the frontmatter, found {len(created)}")
    elif created[0].split(":", 1)[1].strip().strip("\"'") != date:
        err(1, f"created: {created[0].split(':', 1)[1].strip()} != filename date {date}")
    fence, h1_seen, titles, current_h1 = None, {}, [], ""
    nonempty = lambda i: next((l for l in lines[i:] if l.strip()), "")
    prev_nonempty = lambda i: next((l for l in reversed(lines[:i - 1]) if l.strip()), "")
    for i, line in enumerate(lines, 1):
        if i <= fm_end:
            if LOCAL_ABS.search(line):
                err(i, "absolute local path in frontmatter; use a vault-relative link")
            continue
        s = line.lstrip()
        if (m := FENCE.match(s)):
            if fence is None:
                fence, fence_open = s[len(m.group(1)):].strip(), m.group(1)
            elif m.group(1)[0] == fence_open[0] and len(m.group(1)) >= len(fence_open):
                fence = None
            continue
        if fence == "sim-tracker":
            if "[[" in line:
                err(i, "wikilink inside sim-tracker fence; write the bare date")
            continue
        if LOCAL_ABS.search(line):
            err(i, "absolute local path; use a code_root-relative pointer")
        if REMOTE_ABS.search(line):
            warn(i, "remote HPC path outside a sim-tracker fence; point at the tracker's workdir")
        if fence is not None:
            continue
        if line.startswith("# "):
            m, name = TITLE.match(line), line[2:].strip()
            if m:
                titles.append(i)
                if m.group(1) != date:
                    err(i, f"H1 date {m.group(1)} != filename date {date}")
            elif name in H1_ALLOWED:
                if name in h1_seen:
                    err(i, f"'# {name}' appears twice (first at line {h1_seen[name]})")
                h1_seen.setdefault(name, i)
            else:
                err(i, f"top-level heading outside the template set: {line!r}")
            current_h1 = name
        elif NEXT_SUB.match(line) and current_h1 != "Next Steps":
            warn(i, "next-step items belong under the single '# Next Steps' section")
        for m in NOTE_LINK.finditer(line):
            if m.group(6) is None or m.group(6).strip() != m.group(2):
                warn(i, f"intra-project note link should display the bare date {m.group(2)}")
        plain = INLINE_CODE.sub("", line)
        if "\\eqno" in plain:
            err(i, "\\eqno does not render; use \\tag{}")
        if "\\mathbf{" in plain:
            warn(i, "\\mathbf styles a glyph; Math_Notation wants \\vect / \\tens")
        if EMBED.match(line):
            nxt = nonempty(i)
            if not (EMBED.match(nxt) or CAPTION.match(nxt)):
                warn(i, "figure embed not followed by a **Fig. N** caption")
        elif CAPTION.match(line) and EMBED.match(nonempty(i)) and not EMBED.match(prev_nonempty(i)):
            warn(i, "caption sits above its embed; put it directly below")
    if len(titles) != 1:
        err(titles[1] if len(titles) > 1 else 0,
            f"expected one '# YYYY-MM-DD -- Research Note' H1, found {len(titles)}")
    if "Next Steps" not in h1_seen:
        warn(0, "no '# Next Steps' section")
    return out


def selftest() -> None:
    head = ["---", "created: '2026-10-22'", "---", "# 2026-10-22 -- Research Note"]
    cases = [  # (body lines, substring every finding list must contain)
        (["see [[10_Projects/X/10_ResearchNotes/01_NotConfirmed/2026-10-21.md|yesterday]]"], "bare date 2026-10-21"),
        (["| a | [[10_Projects/X/10_ResearchNotes/01_NotConfirmed/2026-10-21\\|y]] |"], "bare date 2026-10-21"),
        (["data at /home/me/run"], "absolute local path"),
        (["data: /scratch/gpfs/T/run/"], "remote HPC path"),
        (["$a \\eqno(1)$"], "\\eqno"),
        (["**Fig. 1** — above", "![[_assets/x.png]]", "text"], "caption sits above"),
        (["![[_assets/x.png]]", "text"], "not followed by a **Fig"),
        (["  ```sim-tracker", "  note: [[2026-10-20]]", "  ```"], "inside sim-tracker fence"),
        (["# Next Steps", "# Next Steps"], "appears twice"),
        (["# Scratch"], "outside the template set"),
        (["# Notes", "## Next steps", "- x", "# Next Steps"], "belong under the single"),
        (["---", "created: 2026-10-22", "type: Research note", "---"], "second frontmatter block"),
        (["---", "project: Plasmonic", "type: Research note", "---"], "second frontmatter block"),
        (["# Next Steps", "", "# 2026-10-23 -- Research Note"], "found 2"),
    ]
    assert any("frontmatter" in m for _, _, m in check("\n".join(head[:1] + ["plan: /home/me/ResearchPlan"] + head[1:]), "2026-10-22.md"))
    for body, want in cases:
        msgs = [m for _, _, m in check("\n".join(head + body), "2026-10-22.md")]
        assert any(want in m for m in msgs), f"missed {want!r}: {msgs}"
    good = head + ["# Notes", "Replaced `\\eqno` with `\\tag{1}`; `\\mathbf{r}` became `\\vect{r}`.",
                   "Or ``\\eqno`` in double backticks.", "```yaml", "---", "ratio: 0.5", "---", "```",
                   "````markdown", "```yaml", "---", "created: 2026-10-22", "---", "```", "````",
                   "---", "data: `20_Data/run/`", "---",
                   "![[_assets/a.png]]", "", "**Fig. 1** — a.", "", "![[_assets/b.png]]", "", "**Fig. 2** — b.",
                   "```bash", "cd run/", "```", "# Next Steps", "- x"]
    assert check("\n".join(good), "2026-10-22.md") == [], check("\n".join(good), "2026-10-22.md")
    assert any("frontmatter" in m for _, _, m in check("created: 2026-10-22\n# x", "2026-10-22.md"))
    assert any("filename" in m for _, _, m in check("\n".join(head), "2026-10-22"))
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    path = Path(sys.argv[1])
    findings = check(path.read_text(encoding="utf-8"), path.name)
    for level, n, msg in findings:
        print(f"{level}: {path.name}:{n}: {msg}")
    sys.exit(1 if any(l == "ERROR" for l, _, _ in findings) else 0)
