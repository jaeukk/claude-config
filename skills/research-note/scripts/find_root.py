#!/usr/bin/env python3
"""Resolve which project's research note a session should write, from any cwd.

Usage: find_root.py [--cwd DIR] [--project NAME] [--note] [--selftest]
Prints the project's vault notes root <root> (or, with --note, today's note path)
and exits 0. Exit 1: no match; candidate projects are listed on stderr. Exit 2:
today's note is already in 02_Confirmed (frozen).

Order: --project name > cwd inside the vault > a code-side CLAUDE.md/AGENTS.md/
README.md above cwd with a "Notes root (WSL)" row or notes_root: > a vault
project README whose code_root: contains cwd.
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys
from pathlib import Path

ROOT_ROW = re.compile(r"Notes root[^\n]*?\(WSL\)[^\n]*?`([^`]+)`")
NOTES_KEY = re.compile(r"^notes_root:\s*(.+?)\s*$", re.M)
CODE_KEY = re.compile(r"^code_root:\s*(.+?)\s*$", re.M)
BUCKETS = ("10_Active", "20_Pending", "30_Proposal", "90_Completed")


def vault(home: Path) -> Path | None:
    """The vault base, resolved as the zotero-obsidian-sync skill prescribes."""
    pin = home / ".claude" / "zotero-obsidian.local"
    pinned = [os.environ.get("OBSIDIAN_VAULT", ""), pin.read_text().strip() if pin.exists() else ""]
    pinned = [c for c in pinned if c and (Path(c) / "20_Notes_Policy.md").exists()]
    if pinned:
        return Path(pinned[0]).resolve()
    auto = glob.glob("/mnt/[a-z]/Users/*/My Drive/_WORKSPACE/20_Notes") + glob.glob("/mnt/[a-z]/My Drive/_WORKSPACE/20_Notes") + [str(home / "20_Notes")]
    found = sorted({str(Path(c).resolve()) for c in auto if (Path(c) / "20_Notes_Policy.md").exists()})
    if len(found) > 1:
        raise SystemExit("several vaults found; set $OBSIDIAN_VAULT or ~/.claude/zotero-obsidian.local:\n  " + "\n  ".join(found))
    return Path(found[0]) if found else None


def projects(v: Path) -> list[Path]:
    return sorted(p for b in BUCKETS for p in (v / "10_Projects" / b).glob("*/") if p.is_dir())


def resolve(cwd: Path, home: Path, project: str | None = None) -> Path | list[Path]:
    """Return the project's notes root, or the candidate list when unresolved."""
    v = vault(home)
    if v is None:
        return []
    projs = projects(v)
    if project:  # vault name, bucket/name, or the code-side folder name via code_root:
        want = project.lower().strip("/")
        hits = [p for p in projs if p.name.lower() == want or f"{p.parent.name}/{p.name}".lower() == want]
        hits += [p for p in projs if p not in hits and (p / "README.md").exists()
                 and (m := CODE_KEY.search((p / "README.md").read_text(errors="ignore")))
                 and Path(m.group(1).strip().strip("\"'")).name.lower() == want]
        return hits[0] if len(hits) == 1 else (hits or projs)
    cwd = cwd.resolve()
    try:  # inside the vault: the 10_Projects/<bucket>/<Project> prefix
        rel = cwd.relative_to(v)
        if len(rel.parts) >= 3 and rel.parts[0] == "10_Projects" and rel.parts[1] in BUCKETS:
            return v / Path(*rel.parts[:3])
    except ValueError:
        pass
    for d in (cwd, *cwd.parents):  # code side: an instruction file naming the notes root
        for name in ("CLAUDE.md", "AGENTS.md", "README.md"):
            f = d / name
            if f.exists():
                t = f.read_text(errors="ignore")
                m = ROOT_ROW.search(t) or NOTES_KEY.search(t)
                if m:
                    r = Path(os.path.expanduser(m.group(1).strip().strip("\"'")))
                    r = r if r.is_absolute() else v / r
                    if r.exists():
                        return r.resolve()
        if d == home:
            break
    best = None  # reverse: the vault README whose code_root is the deepest ancestor of cwd
    for p in projs:
        rd = p / "README.md"
        if rd.exists() and (m := CODE_KEY.search(rd.read_text(errors="ignore"))):
            code = (home / m.group(1).strip().strip("\"'")).resolve()
            if (cwd == code or code in cwd.parents) and (best is None or len(code.parts) > len(best[0].parts)):
                best = (code, p)
    return best[1] if best else projs


def note_path(root: Path, today: str) -> tuple[Path, bool]:
    """(path, frozen): the confirmed copy if it exists, else the working-folder path."""
    confirmed = root / "10_ResearchNotes" / "02_Confirmed" / f"{today}.md"
    if confirmed.exists():
        return confirmed, True
    return root / "10_ResearchNotes" / "01_NotConfirmed" / f"{today}.md", False


def selftest() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp); v = home / "20_Notes"; proj = v / "10_Projects" / "10_Active" / "Multi"
        (proj / "10_ResearchNotes" / "02_Confirmed").mkdir(parents=True)
        (v / "20_Notes_Policy.md").write_text("")
        (proj / "README.md").write_text("---\ncode_root: 10_Projects/10_Active/multi\n---\n")
        code = home / "10_Projects" / "10_Active" / "multi" / "30_Codes"; code.mkdir(parents=True)
        other = home / "10_Projects" / "10_Active" / "plasmon"; other.mkdir(parents=True)
        (other / "CLAUDE.md").write_text(f"| **Notes root `<root>`** (WSL) | `{proj}/` |\n")
        os.environ["OBSIDIAN_VAULT"] = str(v)
        assert resolve(proj / "10_ResearchNotes", home) == proj, "vault cwd"
        assert resolve(code, home) == proj, "code_root reverse lookup"
        assert resolve(other, home) == proj, "CLAUDE.md row"
        assert resolve(home, home, "multi") == proj, "--project by code folder name"
        assert resolve(home, home, "MULTI") == proj, "--project by vault name"
        assert resolve(home / "elsewhere", home) == [proj], "unresolved lists candidates"
        child = v / "10_Projects" / "10_Active" / "Child"; child.mkdir()
        (child / "README.md").write_text("---\ncode_root: 10_Projects/10_Active/multi/30_Codes\n---\n")
        assert resolve(code / "src", home) == child, "deepest code_root wins"
        (child / "README.md").write_text("---\ncode_root: 10_Projects/10_Active/Multi\n---\n")
        assert isinstance(resolve(home, home, "multi"), list), "vault name + code alias is ambiguous"
        assert resolve(home, home, "10_Active/Child") == child, "bucket/name form"
        (v / "10_Projects" / "20_Pending" / "Child").mkdir(parents=True)
        assert isinstance(resolve(home, home, "child"), list), "same name in two buckets is ambiguous"
        v2 = home / "20_Notes_b"; v2.mkdir(); (v2 / "20_Notes_Policy.md").write_text("")
        os.environ.pop("OBSIDIAN_VAULT"); import glob as g
        real = g.glob; g.glob = lambda pat: [str(v), str(v2)] if "20_Notes" in pat else real(pat)
        try:
            resolve(home, home); raise AssertionError("two vaults must not pick silently")
        except SystemExit as e:
            assert "several vaults" in str(e)
        g.glob = real; os.environ["OBSIDIAN_VAULT"] = str(v)
        (proj / "10_ResearchNotes" / "02_Confirmed" / "2026-10-22.md").write_text("")
        assert note_path(proj, "2026-10-22")[1] is True and note_path(proj, "2026-10-23")[1] is False
    print("selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd()); ap.add_argument("--project")
    ap.add_argument("--note", action="store_true"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    r = resolve(Path(a.cwd), Path.home(), a.project)
    if isinstance(r, list):
        print("unresolved; candidates:\n" + "\n".join(f"  {p.name}  ({p.parent.name})" for p in r)
              if r else "no vault found", file=sys.stderr)
        sys.exit(1)
    if not a.note:
        print(r); sys.exit(0)
    p, frozen = note_path(r, dt.date.today().isoformat())
    print(p)
    sys.exit(2 if frozen else 0)
