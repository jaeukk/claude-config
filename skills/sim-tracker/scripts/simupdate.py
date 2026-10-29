#!/usr/bin/env python3
"""Mechanical reconciliation, verification and closure of sim-tracker blocks (the sim-update skill).

Usage: simupdate.py [run] [--project NAME | --root DIR | --all] [--dry-run] [--no-poll]
       simupdate.py accept KEY --checks TEXT [--mail FILE] [--archived] [--ledger-checked] [scope]
       simupdate.py override KEY --by OWNER --reason TEXT [scope]
       simupdate.py register KEY [scope]
       simupdate.py --selftest

run       Reconcile <root>/20_Progress/SimulationRegistry.md with every tracker block; poll
          unverified jobs (simtrack.py); run each tracker's declared outputs/logs checks on its file
          endpoint; where every gate is mechanical and passes, write `verified`, a verified_success
          closure, and move the registry row; list everything that needs judgment.
accept    After the caller has done a tracker's judgment items: rerun its gates and record the
          verification together with the caller's statement of what was checked.
override  Close a tracker as owner_override; only on the owner's explicit decision.
register  Add the Active registry row of a valid, unclosed tracker.

Exit status: 0 done; 1 notes root, registry or named tracker unusable; 3 an SSH endpoint unavailable.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import socket
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import simtrack as st

FILE_HOST = {"nurion": "nurion-dm", "neuron": "neuron-dm", "della": "della"}  # outputs/logs endpoint
MECHANICAL = {"outputs", "logs", "reject_regex"}  # verify keys this script checks; others need judgment
REGISTRY = "20_Progress/SimulationRegistry.md"
WIKI = re.compile(r"\[\[([^\]|\\]+?)(?:\\?\|[^\]]*)?\]\]")
RAW_ID = re.compile(r"""^\s*["']?(?:id|tracker_id)["']?\s*:\s*["']?([^"'\n#]+?)["']?\s*$""", re.M)
SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?")

# Runs on the file endpoint (Nurion's data mover has Python 3.6). The spec arrives base64-encoded in
# argv, so no tracker text is ever interpreted by a shell.
CHECKER = r'''
import base64, glob, json, os, re, sys
res = []
for t in json.loads(base64.b64decode(sys.argv[1]).decode()):
    for c in t["checks"]:
        r = {"key": t["key"], "i": c["i"], "missing": [], "n": 0, "sample": [], "empty": [], "hits": {}}
        try:
            def at(p):
                return p if os.path.isabs(p) else os.path.join(t["workdir"], p)
            if c["paths"] is not None:
                names = [at(p) for p in c["paths"]]
                found = [f for f in names if os.path.isfile(f)]
                r["missing"] = [f for f in names if not os.path.isfile(f)]
            else:
                found = sorted(f for f in glob.glob(at(c["glob"]), recursive=True) if os.path.isfile(f))
            r["n"], r["sample"] = len(found), found[:3]
            r["empty"] = [f for f in found if os.path.getsize(f) == 0][:20]
            if c["reject_regex"]:
                rx = re.compile(c["reject_regex"])
                for f in found:
                    n, first = 0, None
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        for k, line in enumerate(fh, 1):
                            if rx.search(line):
                                n += 1
                                first = first or "%d: %s" % (k, line.strip()[:200])
                    if n:
                        r["hits"][f] = [n, first]
        except Exception as e:
            r["error"] = "%s: %s" % (type(e).__name__, e)
        res.append(r)
print(json.dumps(res))
'''


def _cells(line: str) -> list[str]:
    """Split a Markdown table row on pipes outside [[wikilinks]] and not escaped."""
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    out, cur, depth, i = [], "", 0, 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("[[", "]]", "\\|"):
            depth += {"[[": 1, "]]": -1 if depth else 0, "\\|": 0}[two]
            cur, i = cur + two, i + 2
        elif s[i] == "|" and not depth:
            out, cur, i = out + [cur.strip()], "", i + 1
        else:
            cur, i = cur + s[i], i + 1
    return out + [cur.strip()]


@dataclass
class Row:
    """One registry table row."""

    section: str  # "active" or "closed"
    idx: int  # line index in the registry text
    cols: dict[str, str]

    @property
    def key(self) -> str:
        return self.cols.get("tracker_id", "")

    @property
    def target(self) -> str:
        m = WIKI.search(self.cols.get("note", ""))
        return m.group(1).strip() if m else ""


@dataclass
class Registry:
    """SimulationRegistry.md: its lines and the tracker_id tables under Active and Closed headings."""

    path: Path
    text: str
    lines: list[str]
    rows: list[Row]
    tables: dict[str, tuple[list[str], int]]  # section -> (lowercased columns, last table line index)

    @property
    def escaped(self) -> bool:
        return "\\|" in self.text


def read_registry(path: Path, text: str) -> Registry:
    """Parse the registry; a table belongs to the Closed section when its heading says 'closed'."""
    lines = text.splitlines(keepends=True)
    section, cols, rows, tables = "active", None, [], {}
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#"):
            section, cols = ("closed" if "closed" in s.lower() else "active"), None
        elif s.startswith("|") and SEPARATOR.fullmatch(s):
            continue
        elif s.startswith("|"):
            c = _cells(s)
            if i + 1 < len(lines) and SEPARATOR.fullmatch(lines[i + 1].strip()):
                cols = [x.lower() for x in c] if c and c[0].lower() == "tracker_id" else None
                if cols:
                    tables[section] = (cols, i + 1)
            elif cols:
                rows.append(Row(section, i, dict(zip(cols, c, strict=False))))
                tables[section] = (cols, i)
    return Registry(path, text, lines, rows, tables)


def _ledger(d: dict) -> bool:
    """Whether the block declares dynamic successors: a job registry, a controller, a job whose role,
    file or name says controller, or verify.additional naming a controller, successors or a ledger."""
    v = d.get("verify") if isinstance(d.get("verify"), dict) else {}
    return ("job_registry" in d or "controller" in d
            or any(re.search("controller", str(j.get(x, "")), re.I)
                   for j in d["jobs"] for x in ("role", "file", "name"))
            or bool(re.search(r"controller|successor|ledger", str(v.get("additional", "")), re.I)))


def _needs_active(d: dict) -> bool:
    return "closure" not in d and (not d["jobs"] or any(not j.get("verified") for j in d["jobs"]) or _ledger(d))


@dataclass
class Recon:
    """Reconciliation of one notes root: blocks, registry, drift per tracker key, report notes."""

    blocks: list
    bad: list
    reg: Registry | None
    drift: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    notes: list[str] = field(default_factory=list)
    moves: list[str] = field(default_factory=list)  # closed in the block, still listed Active


def reconcile(root: Path) -> Recon:
    """Match registry rows to tracker blocks by tracker key and classify every mismatch.

    Parameters
    ----------
    root : Path
        Project notes root; the vault is three levels up (10_Projects/<bucket>/<project>).

    Returns
    -------
    Recon
        `drift` lists the codes (ORPHAN_NOTE, PATH_MISMATCH, STATIC_MISMATCH, DUPLICATE_TRACKER_ID,
        INDEX_ONLY_CLOSURE) that exclude a tracker from verification, closure and registry writes.
    """
    blocks, _ = st.scan(root, None)
    bad, good = [b for b in blocks if b.error], [b for b in blocks if not b.error]
    p = root / REGISTRY
    reg = read_registry(p, st._read(p)) if p.exists() else None
    r = Recon(blocks, bad, reg)
    if reg is None:
        r.notes.append(f"no registry at {REGISTRY}; verification, closure and registration are disabled")
    vault = root.resolve().parents[2]
    rows, keys, bad_keys = defaultdict(list), defaultdict(list), {}
    for row in reg.rows if reg else []:
        rows[row.key].append(row)
    for b in good:
        keys[b.key].append(b)
    for b in bad:
        for k in RAW_ID.findall(b.body):
            bad_keys.setdefault(k.strip(), b)
    for k, bs in keys.items():
        if len(bs) > 1:
            r.drift[k].append("DUPLICATE_TRACKER_ID: " + ", ".join(b.where for b in bs))
        if len(rows.get(k, [])) > 1:
            r.drift[k].append(f"DUPLICATE_TRACKER_ID: {len(rows[k])} registry rows")
        b, rs = bs[0], rows.get(k, [])
        for row in rs:
            if not row.target or (vault / f"{row.target}.md").resolve() != b.note.resolve():
                r.drift[k].append(f"PATH_MISMATCH: registry {row.target or row.cols.get('note')!r} vs {b.where}")
            for f in ("host", "scheduler"):
                if f in row.cols and row.cols[f] != str(b.data[f]):
                    r.drift[k].append(f"STATIC_MISMATCH: registry {f} {row.cols[f]!r} vs block {b.data[f]!r}")
        active = [x for x in rs if x.section == "active"]
        closed = [x for x in rs if x.section == "closed"]
        if "closure" in b.data:
            r.moves += [k] if active else []
        elif closed:
            r.drift[k].append("INDEX_ONLY_CLOSURE: registry lists it Closed but the block has no closure")
        elif _needs_active(b.data) and not active and reg is not None:
            r.drift[k].append("ORPHAN_NOTE: no Active registry row (simupdate.py register KEY)")
    prov, unval = [], 0
    for k, rs in rows.items():
        if k in keys:
            continue
        for row in rs:
            if k in bad_keys and row.section == "active":
                prov.append(k)
            elif k in bad_keys:
                unval += 1
            else:
                r.notes.append(f"{k}: ORPHAN_REGISTRY ({row.section} row with no tracker block)")
    if prov:
        r.notes.append(f"PROVISIONAL_SCHEMA_ERROR (Active rows backed by malformed blocks): {', '.join(prov)}")
    if unval:
        r.notes.append(f"UNVALIDATED_CLOSED: {unval} retained Closed rows backed by malformed blocks")
    return r


def _sched_gate(o: st.Obs | None, sched: str) -> str:
    """PASS, FAIL, PENDING, NO_RECORD, UNRESOLVED or UNOBSERVED for one job's fresh observation."""
    if o is None:
        return "UNOBSERVED"
    if o.result == "NOT_FOUND":
        return "NO_RECORD"
    u = o.upd
    if o.result != "FOUND" or "state" not in u:
        return "UNRESOLVED"
    s, e = str(u["state"]), u.get("exit")
    if sched == "pbs":
        return ("PASS" if e == 0 else "FAIL") if s == "F" and isinstance(e, int) else (
            "UNRESOLVED" if s == "F" else "PENDING")
    if sched == "slurm":
        if s not in st.SLURM_TERMINAL:
            return "PENDING"
        clean = all(not u.get(f"tasks_{k}") for k in ("missing", "failed", "unknown"))
        return "PASS" if s == "COMPLETED" and e == u.get("derived_exit") == "0:0" and clean else (
            "FAIL" if o.failed else "UNRESOLVED")
    return ("PASS" if e == 0 else "FAIL") if s == "EXITED" and isinstance(e, int) else "PENDING"


SOURCE = {"pbs": "qstat-xf", "slurm": "sacct", "local": "receipt"}


def _cached_fail(j: dict, sched: str) -> bool:
    """Whether the job's recorded (cached) outcome is already a terminal failure."""
    s, e, dx = str(j.get("state")), j.get("exit"), j.get("derived_exit")
    if sched == "slurm":
        return s in st.SLURM_TERMINAL and (s != "COMPLETED" or any(
            x not in (None, "-", "", "0:0") for x in (e, dx)))
    return s == ("F" if sched == "pbs" else "EXITED") and isinstance(e, int) and e != 0


def mech_checks(v: dict) -> tuple[list[dict], list[str]]:
    """Declared outputs/logs checks of a verify contract, and the problems that make any unverifiable."""
    checks, probs = [], []
    for kind in ("outputs", "logs"):
        x = v.get(kind)
        for it in x if isinstance(x, list) else [] if x is None else [x]:
            if not isinstance(it, dict) or ("paths" in it) == ("glob" in it):
                probs.append(f"{kind}: each entry needs exactly one of paths/glob")
                continue
            paths, g, cnt = it.get("paths"), it.get("glob"), it.get("count")
            if not isinstance(cnt, int) or isinstance(cnt, bool) or cnt < 1:
                probs.append(f"{kind}: count must be an integer >= 1 (an empty match never passes)")
                continue
            if paths == []:
                probs.append(f"{kind}: paths is empty")
                continue
            if (paths is not None and not (isinstance(paths, list) and all(isinstance(q, str) for q in paths))) or (
                    g is not None and not isinstance(g, str)):
                probs.append(f"{kind}: paths must be a list of strings, glob a string")
                continue
            rx = it.get("reject_regex", v.get("reject_regex") if kind == "logs" else None)
            try:
                if rx is not None:
                    re.compile(rx)
            except (re.error, TypeError):
                probs.append(f"{kind}: reject_regex {rx!r} does not compile")
                continue
            checks.append({"i": len(checks), "kind": kind, "paths": paths, "glob": g, "count": cnt,
                           "reject_regex": rx})
    if "reject_regex" in v and not v.get("logs"):
        probs.append("verify-level reject_regex with no logs to apply it to")
    return checks, probs


def _is_local_machine(d: dict) -> bool:
    m = d.get("machine") if isinstance(d.get("machine"), dict) else {}
    try:
        mid = Path("/etc/machine-id").read_text().strip()
    except OSError:
        return False
    return m.get("hostname") == socket.gethostname() and str(m.get("machine_id")) == mid


def app_checks(run: st.Run, todo: dict[str, tuple[st.Block, list[dict]]]) -> dict[str, list[dict] | None]:
    """Run declared checks, batched per file endpoint; {key: results, or None if not observed}."""
    by_host: dict[str | None, list] = defaultdict(list)
    out: dict[str, list[dict] | None] = {}
    for k, (b, checks) in todo.items():
        host = FILE_HOST.get(str(b.data["host"]))
        if host is None and not _is_local_machine(b.data):
            run.lines.append(f"{k}: outputs/logs live on {b.data['host']}; run sim-update on that machine")
            out[k] = None
            continue
        by_host[host].append({"key": k, "workdir": str(b.data.get("workdir", "")), "checks": checks})
    for host, spec in by_host.items():
        b64 = base64.b64encode(json.dumps(spec).encode()).decode()
        cmd = f"python3 - {b64} <<'SIM_CHECKER_EOF'\n{CHECKER}\nSIM_CHECKER_EOF"
        keys = [t["key"] for t in spec]
        if host is None:
            t0 = st._now()
            r = subprocess.run(["bash", "-s"], input=f"timeout 600 {cmd}\n".encode(), capture_output=True)
            c = run.save(socket.gethostname(), "appcheck", cmd, keys, r.returncode, r.stdout, r.stderr, t0)
        elif not st.LIVE(host):
            run.dead[host] = "no live master socket"
            c = None
        else:
            c = run.query(host, "appcheck", cmd, keys, limit=600)
        res = None
        if c is not None and c.rc == 0:
            try:
                res = json.loads(c.out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                run.lines.append(f"{host or 'local'} appcheck: unreadable output; see {c.path}")
        for k in keys:
            out[k] = [x for x in res if x["key"] == k] if res is not None else None
    return out


def app_gate(checks: list[dict], res: list[dict] | None) -> tuple[str, list[str]]:
    """PASS or FAIL with reasons, or UNOBSERVED; every declared path and exact count must match."""
    if res is None or len(res) != len(checks):
        return "UNOBSERVED", []
    fails, got = [], {r["i"]: r for r in res}
    for c in checks:
        r, what = got[c["i"]], f"{c['kind']} {c['glob'] or ', '.join(c['paths'])}"
        if r.get("error"):
            fails.append(f"{what}: {r['error']}")
        elif r["missing"]:
            fails.append(f"{what}: {len(r['missing'])} declared paths missing, e.g. {r['missing'][0]}")
        elif r["n"] != c["count"]:
            fails.append(f"{what}: {r['n']} files, expected exactly {c['count']}")
        elif r["hits"]:
            f, (n, first) = next(iter(r["hits"].items()))
            fails.append(f"{what}: reject_regex hits in {len(r['hits'])} files, e.g. {f} ({n}x) line {first}")
    return ("FAIL" if fails else "PASS"), fails


def _archived(root: Path, d: dict, j: dict) -> tuple[str | None, str]:
    """Latest indexed capture with a record for job j, if that record is terminal success.

    Checks the provenance a capture must carry (indexed path and stdout hash, command, endpoint
    timestamps, exit status) and re-reads its raw stdout. Later executions and output ownership are
    the caller's judgment (accept --archived).
    """
    jid, sched = str(j["id"]), d["scheduler"]
    alias = st.ENDPOINTS[(str(d["host"]), str(sched))]
    query, ok_rc = {"pbs": ("qstat -xf ", (0, 153)), "slurm": ("sacct ", (0,))}[sched]  # 153: some IDs unknown
    pat = re.compile(r"^- `([^`]+\.json)`: \S+ (\S+); IDs ([^;]*);.*stdout SHA-256 `([0-9a-f]{64})`")
    latest = None
    for idx in sorted((root / "20_Progress").glob("SimulationEvidence_*.md")):
        for line in st._read(idx).splitlines():
            m = pat.match(line)
            if not m or m.group(2) != {"pbs": "qstat", "slurm": "sacct"}.get(sched) or jid not in [
                    x.strip() for x in m.group(3).split(",")]:
                continue
            try:
                meta = json.loads((root / m.group(1)).read_text(encoding="utf-8"))
                raw = (root / m.group(1)).with_name(meta["stdout"]).read_bytes()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if st._sha(raw) != m.group(4) or meta.get("stdout_sha256") != m.group(4) or not (
                    meta.get("endpoint") == alias and query in str(meta.get("command"))
                    and jid in (meta.get("ids") or []) and meta.get("endpoint_start") and meta.get("endpoint_end")
                    and meta.get("exit_status") in ok_rc):
                continue
            text = raw.decode(errors="replace")
            if sched == "pbs":
                rec = st.qstat_records(text).get(jid.split(".")[0])
                o = st.Obs({"state": rec.get("job_state"), "exit": int(rec["Exit_status"]) if re.fullmatch(
                    r"-?\d+", rec.get("Exit_status", "")) else None}, "FOUND") if rec else None
            else:
                o = st.slurm_job(jid, j.get("array"), {}, st.sacct_rows(text), False)
            if o is not None and o.result == "FOUND":
                latest = (m.group(1), _sched_gate(o, sched))
    if latest is None:
        return None, "no indexed capture under 20_Progress/sim-evidence/ holds a record for it"
    return (latest[0], "") if latest[1] == "PASS" else (None, f"latest archived record ({latest[0]}) is {latest[1]}")


@dataclass
class Ack:
    """What an accept call asserts the caller checked."""

    checks: str = ""
    mail: dict[str, int] = field(default_factory=dict)  # PBS job ID, exactly as in the tracker -> Exit_status
    mail_raw: bytes = b""
    archived: bool = False
    ledger: bool = False


def _closure_reason(d: dict, srcs: list[str], checks: list[dict], res: list[dict] | None, ack: Ack | None) -> str:
    n = len(d["jobs"])
    if not srcs:
        return f"{n}/{n} jobs were already verified" + (f"; checks: {ack.checks}" if ack and ack.checks else "")
    app = "; ".join(f"{c['kind']} {r['n']}/{c['count']}" + (", 0 reject hits" if c["reject_regex"] else "")
                    for c, r in zip(checks, sorted(res or [], key=lambda x: x["i"]), strict=False))
    return (f"{n}/{n} jobs verified ({', '.join(sorted(set(srcs)))}); {app or 'no outputs/logs declared'}"
            + (f"; checks: {ack.checks}" if ack and ack.checks else ""))


def _row_cells(reg: Registry, section: str, b: st.Block, vault: Path, extra: dict) -> str:
    """A registry row for block b in the given table's column layout."""
    cols = reg.tables[section][0]
    rel = b.note.resolve().relative_to(vault).with_suffix("").as_posix()
    sep = "\\|" if reg.escaped else "|"
    link = f"[[{rel}{sep}{b.note.stem}]]"
    vals = {"tracker_id": b.key, "note": link, "host": str(b.data["host"]), "scheduler": str(b.data["scheduler"]),
            **extra}
    clean = [re.sub(r"\s+", " ", str(vals.get(c, ""))).replace("|", "\\|") if c != "note" else vals["note"]
             for c in cols]
    return "| " + " | ".join(clean) + " |\n"


def _registry_edit(reg: Registry, key: str, b: st.Block, vault: Path, closure: dict | None) -> str:
    """Registry text after moving key's Active row(s) to Closed (closure given) or adding an Active row."""
    rows = [r for r in reg.rows if r.key == key]
    if closure is None:
        _, last = reg.tables["active"]
        return "".join(reg.lines[:last + 1] + [_row_cells(reg, "active", b, vault, {})] + reg.lines[last + 1:])
    dels = {r.idx for r in rows if r.section == "active"}
    lines = [x for i, x in enumerate(reg.lines) if i not in dels]
    if not any(r.section == "closed" for r in rows):
        extra = {"closed": closure["mode"], "evidence": closure["reason"][:300]}
        if "closed" in reg.tables:
            last = reg.tables["closed"][1]
            ins = last + 1 - sum(d <= last for d in dels)
            lines[ins:ins] = [_row_cells(reg, "closed", b, vault, extra)]
        else:
            reg.tables["closed"] = (["tracker_id", "note", "closed", "evidence"], 0)
            lines += ["\n## Closed since this registry began\n\n", "| tracker_id | note | closed | evidence |\n",
                      "|---|---|---|---|\n", _row_cells(reg, "closed", b, vault, extra)]
    return "".join(lines)


def _write_registry(reg: Registry, new: str) -> bool:
    """Atomically replace the registry if it is unchanged since it was read."""
    if st._read(reg.path) != reg.text:
        return False
    tmp = reg.path.with_name(f".{reg.path.name}.simupdate.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    tmp.replace(reg.path)
    return True


def process(root: Path, dry: bool, poll: bool, only: str | None = None, ack: Ack | None = None,
            override: dict | None = None, register: bool = False) -> tuple[list[str], int]:
    """Reconcile, poll, gate, and write verification/closure/registry changes for one notes root.

    Parameters
    ----------
    root : Path
        Project notes root.
    dry : bool
        Query and report; write nothing.
    poll : bool
        Poll schedulers first (fresh scheduler evidence is required for any verification).
    only : str, optional
        Act on this tracker key alone (accept, override, register).
    ack, override, register
        The accept assertions, an owner_override closure {by, reason}, or a registration request.

    Returns
    -------
    (list of str, int)
        Report lines and the exit status (0, 1 if the named tracker is not actionable, 3 if an
        endpoint was unavailable).
    """
    out, rc = [], 0
    rec = reconcile(root)
    if not rec.blocks and rec.reg is None:
        return [], 0  # not a simulation project
    run = st.Run(root, dry, label="sim-update")
    inv = {"blocks": [{"note": b.where, "sha256": b.sha256, "key": None if b.error else b.key, "error": b.error}
                      for b in rec.blocks],
           "registry_rows": [{"section": r.section, **r.cols} for r in rec.reg.rows] if rec.reg else [],
           "drift": dict(rec.drift), "notes": rec.notes}
    c = run.save("vault", "reconcile", "registry/tracker reconciliation", [], 0,
                 json.dumps(inv, indent=1).encode(), b"", st._now())
    n_rows = len(rec.reg.rows) if rec.reg else 0
    out.append(f"reconcile: {len(rec.blocks)} fences ({len(rec.bad)} quarantined), {n_rows} registry rows; "
               f"inventory {c.path}{' (dry run, not saved)' if dry else ''}")
    out += [f"{k}: EXCLUDED ({'; '.join(v)})" for k, v in rec.drift.items() if not only or k == only]
    out += rec.notes if not only else []
    fresh: dict = {}
    if poll and not override and not register:
        lines, dead = st.poll_root(root, None, dry, fresh)
        out += lines
        rc = 3 if dead else rc
    blocks, texts = st.scan(root, None)
    by_key = defaultdict(list)
    for b in blocks:
        if not b.error:
            by_key[b.key].append(b)
    vault = root.resolve().parents[2]
    reg = rec.reg
    blocking = [x for x in rec.drift.get(only or "", []) if not (register and x.startswith("ORPHAN_NOTE"))]
    if only and (len(by_key.get(only, [])) != 1 or blocking):
        out.append(f"{only}: not actionable (no unique valid block, or excluded above)")
        return out, 1
    if reg is None:
        out.append("no registry: no verification, closure or registration (ask the owner where it is)")
        return out, 1 if only else rc
    if register:
        b = by_key[only][0]
        if any(r.key == only for r in reg.rows) or "closure" in b.data:
            out.append(f"{only}: already registered or closed; nothing to add")
            return out, 1
        warn = "" if isinstance(b.data.get("verify"), dict) else " (UNVERIFIABLE until it declares verify:)"
        if dry or "active" not in reg.tables:
            out.append(f"{only}: would add Active row" if dry else "registry has no Active table; add one by hand")
            return out, 0 if dry else 1
        ok = _write_registry(reg, _registry_edit(reg, only, b, vault, None))
        out.append(f"{only}: Active row added{warn}" if ok else "registry changed during the run; rerun")
        return out, 0 if ok else 1
    bad = [b for b in blocks if b.error]
    at, writes, closing, reached = st._now(), {}, set(), set()
    todo, plan = {}, {}
    for k, bs in by_key.items():
        b = bs[0]
        d = b.data
        if (only and k != only) or k in rec.drift or "closure" in d:
            continue
        if override:
            plan[k] = ("override", None)
            continue
        if st._overlaps(st.screen_terms(d), bad):
            continue  # poll_root already reported the exclusion
        pend = [(i, j) for i, j in enumerate(d["jobs"]) if not j.get("verified")]
        v = d.get("verify")
        if not d["jobs"]:
            out.append(f"{k} ({b.where}): NO_JOBS; stays Active until jobs are recorded")
            continue
        if not isinstance(v, dict) or not v:
            out.append(f"{k} ({b.where}): UNVERIFIABLE: no verify contract; declare one (never invented here)")
            continue
        checks, probs = mech_checks(v)
        if checks and not str(d.get("workdir", "")).startswith("/"):
            checks, probs = [], probs + ["outputs/logs declared but workdir is missing or not absolute"]
        judge = {x: v[x] for x in v if x not in MECHANICAL}
        if probs:
            out.append(f"{k} ({b.where}): UNVERIFIABLE: malformed declared checks ({'; '.join(probs)}); "
                       "fix the verify contract")
            continue
        if not checks and not judge:
            out.append(f"{k} ({b.where}): UNVERIFIABLE: verify declares no checks ({'; '.join(probs)})")
            continue
        gates = {i: _sched_gate(fresh.get(k, {}).get(i), str(d["scheduler"])) for i, _ in pend}
        gates = {i: "FAIL (cached; history purged)" if g == "NO_RECORD" and _cached_fail(d["jobs"][i], str(
            d["scheduler"])) else g for i, g in gates.items()}
        plan[k] = ("gate", (b, pend, gates, checks, judge))
        if all(g in ("PASS", "NO_RECORD") for g in gates.values()) and checks:  # F4: also when all verified
            todo[k] = (b, checks)
    results = app_checks(run, todo) if todo else {}
    mail_cap = run.save("gmail", "mail", "exact-ID PBS end-mail records supplied by the caller", sorted(ack.mail),
                        0, ack.mail_raw, b"", st._now()).path if ack and ack.mail else ""
    for k, (kind, info) in plan.items():
        b = by_key[k][0]
        d, new = b.data, copy.deepcopy(b.data)
        if kind == "override":
            new["closure"] = {"mode": "owner_override", "by": override["by"], "at": at, "reason": override["reason"]}
            verdict = "owner_override"
        else:
            b, pend, gates, checks, judge = info
            fails = [f"{d['jobs'][i]['id']} {g}" for i, g in gates.items() if g.startswith("FAIL")]
            wait = [f"{d['jobs'][i]['id']} {g}" for i, g in gates.items()
                    if g in ("PENDING", "UNRESOLVED", "UNOBSERVED")]
            if fails:
                out.append(f"{k} ({b.where}): FAILED: {', '.join(fails)}; stays open (owner override or resubmit)")
                continue
            if wait:
                out.append(f"{k} ({b.where}): WAITING: {', '.join(wait)}")
                continue
            ag, why = app_gate(checks, results.get(k)) if checks else ("NONE", [])
            if ag == "FAIL":
                out.append(f"{k} ({b.where}): APP CHECK FAILED: {'; '.join(why)}")
                continue
            if ag == "UNOBSERVED":
                out.append(f"{k} ({b.where}): WAITING: outputs/logs not observed")
                continue
            srcs, need, norec = {}, [], []
            for i, g in gates.items():
                jid = str(d["jobs"][i]["id"])
                if g == "PASS":
                    srcs[i] = SOURCE[str(d["scheduler"])]
                elif ack and str(d["scheduler"]) == "pbs" and jid in ack.mail:
                    if ack.mail[jid] != 0:
                        need.append(f"{jid}: end mail Exit_status {ack.mail[jid]} (failure)")
                    srcs[i] = f"gmail:{mail_cap}"
                elif ack and ack.archived and (cap := _archived(root, d, d["jobs"][i]))[0]:
                    srcs[i] = f"archived:{cap[0]}"
                elif ack and ack.archived:
                    need.append(f"{jid}: {_archived(root, d, d['jobs'][i])[1]}")
                else:
                    norec.append(jid)
            if norec:
                need.append(f"no fresh scheduler record for {', '.join(norec)}: search Gmail label HPC notices/Nurion "
                            "for exact-ID end mail (--mail FILE), or check later executions and output ownership "
                            "for an archived capture (--archived)")
            if judge and not (ack and ack.checks):
                need.append("verify items needing judgment: " + json.dumps(judge, default=str, ensure_ascii=False))
            if _ledger(d) and not (ack and ack.ledger):
                need.append("controller/ledger declared: account for every successor in its ledger, each listed "
                            "and verified (--ledger-checked)")
            if need:
                flag = " --ledger-checked" if _ledger(d) else ""
                out.append(f"{k} ({b.where}): JUDGMENT NEEDED: " + " | ".join(need)
                           + f" -> after checking: simupdate.py accept {k} --checks \"<what you checked>\"{flag}"
                           + " [--mail FILE] [--archived]")
                continue
            stamp = "+".join(x for x in ("outputs" if any(c["kind"] == "outputs" for c in checks) else "",
                                         "logs" if any(c["kind"] == "logs" for c in checks) else "",
                                         "checks" if ack and ack.checks else "") if x)
            for i, s in srcs.items():
                new["jobs"][i]["verified"] = f"{s}{'+' + stamp if stamp else ''}@{at}"
            reason = _closure_reason(new, list(srcs.values()), checks, results.get(k), ack)
            new["closure"] = {"mode": "verified_success", "by": "simupdate.py", "at": at, "reason": reason}
            verdict = "verified_success"
        state, nb = st.stage(b, new, dry)
        if nb is not None or state.startswith(("PROPOSED ONLY (02_Confirmed", "DRY RUN")):
            reached.add(k)
        out.append(f"{k} ({b.where}): {verdict.upper()} -> {state}")
        if nb is not None:
            writes.setdefault(b.note, []).append((b, nb))
            closing.add(k)
    out += st.write_notes(texts, writes)
    post = defaultdict(list)
    for b in st.scan(root, None)[0] if not dry else []:
        if not b.error:
            post[b.key].append(b)
    for k, bs in post.items():  # the block's closure is the authority; the registry follows it
        if len(bs) != 1 or "closure" not in bs[0].data or k in rec.drift or (only and k != only):
            continue
        cur = read_registry(reg.path, st._read(reg.path))
        listed = [r for r in cur.rows if r.key == k]
        if not any(r.section == "active" for r in listed) and (listed or k not in closing):
            continue
        ok = _write_registry(cur, _registry_edit(cur, k, bs[0], vault, bs[0].data["closure"]))
        out.append(f"{k}: registry row moved to Closed" if ok else f"{k}: registry changed during the run; rerun")
    if only and only not in reached:
        rc = max(rc, 1)  # accept/override recorded nothing
    out[1:1] = run.lines
    out += [f"{h}: UNAVAILABLE ({why}); ask the user to open it (`ssh {h}`, OTP, leave it open); do not retry"
            for h, why in run.dead.items()]
    return out, 3 if run.dead and not rc else rc


def _mail(path: str) -> dict[str, int]:
    """Exact-ID PBS end-mail records written by the caller: [{"id": ..., "exit_status": int, ...}]."""
    recs = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(recs, list) or not all(
            isinstance(x, dict) and isinstance(x.get("id"), str) and type(x.get("exit_status")) is int
            and x.get("message") for x in recs):
        raise SystemExit("--mail: expected a JSON list of {id: str, exit_status: int, message: <mail id>, ...}")
    out: dict[str, int] = {}
    for x in recs:
        if out.get(x["id"], x["exit_status"]) != x["exit_status"]:
            raise SystemExit(f"--mail: conflicting exit statuses for {x['id']}")
        out[x["id"]] = x["exit_status"]
    return out


def selftest() -> None:
    """Offline end-to-end check: fake schedulers, real outputs/logs checker run locally."""
    import ast
    import tempfile
    ast.parse(CHECKER, feature_version=(3, 6))  # the data mover runs Python 3.6
    assert _cells(r"| a | [[x/y\|2026]] | b |") == ["a", r"[[x/y\|2026]]", "b"]
    assert _cells("| a | [[x/y|2026]] | b |") == ["a", "[[x/y|2026]]", "b"], "unescaped wikilink pipe"
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "V/10_Projects/10_Active/P"
    nc, cf = root / "10_ResearchNotes/01_NotConfirmed", root / "10_ResearchNotes/02_Confirmed"
    prog = root / "20_Progress"
    for p in (nc, cf, prog):
        p.mkdir(parents=True)
    w = tmp / "work"
    w.mkdir()
    for f, s in (("out1.txt", "1"), ("out2.txt", "2"), ("run.log", "ok\n"), ("bad.log", "x\nTraceback y\n")):
        (w / f).write_text(s)

    def blk(key: str, job: str, verify: str, host: str = "nurion", sched: str = "pbs") -> str:
        return (f"```sim-tracker\nid: {key}\nhost: {host}\nscheduler: {sched}\nworkdir: {w}\nstatus: running\n"
                f"verify:\n{verify}jobs:\n  - {{id: {job}, state: R}}\n```\n\n")
    ok = "  outputs: {glob: \"out*.txt\", count: 2}\n  logs: {paths: [run.log], count: 1, reject_regex: Traceback}\n"
    judged = ok + "  additional: \"Require X.\"\n"
    nc_text = ("# n\n\n" + blk("t-auto", "100.pbs", ok) + blk("t-fail", "101.pbs", ok) + blk("t-nr", "102.pbs", ok)
               + blk("t-badapp", "103.pbs", "  logs: {paths: [bad.log], count: 1, reject_regex: Traceback}\n")
               + blk("t-judge", "500", judged, "della", "slurm") + blk("t-arch", "105.pbs", judged)
               + blk("t-orphan", "106.pbs", ok) + blk("t-path", "107.pbs", ok)
               + blk("t-count", "108.pbs", "  outputs: {glob: \"out*.txt\", count: 3}\n")
               + blk("t-ledger", "109.pbs", ok).replace("status: running", "status: running\njob_registry: l.jsonl")
               + blk("t-dup", "110.pbs", ok) + blk("t-dup", "111.pbs", ok) + blk("t-static", "112.pbs", ok)
               + blk("t-idx", "113.pbs", ok)
               + blk("t-miss", "114.pbs", "  logs: {paths: [run.log, nope.log], count: 1}\n")
               + blk("t-cfail", "115.pbs", ok).replace("{id: 115.pbs, state: R}", "{id: 115.pbs, state: F, exit: 1}")
               + blk("t-pend", "117.pbs", ok).replace("status: running", "closure: {mode: verified_success, by: x, "
                                                       "at: t, reason: interrupted before the registry move}")
               + blk("t-badrx", "118.pbs", ok.replace("reject_regex: Traceback", "reject_regex: \"(\""))
               + blk("t-zero", "119.pbs", "  outputs: {glob: \"missing*\", count: 0}\n")
               + blk("t-ctl", "120.pbs", ok).replace("{id: 120.pbs, state: R}",
                                                     "{id: 120.pbs, state: R, file: controller.sh}")
               + blk("t-snr", "501", ok, "della", "slurm")
               + blk("t-allbad", "121.pbs", "  outputs: {glob: \"nothing*\", count: 1}\n").replace(
                   "{id: 121.pbs, state: R}", "{id: 121.pbs, state: F, exit: 0, verified: \"x@t\"}")
               + blk("t-allver", "116.pbs", ok).replace("{id: 116.pbs, state: R}",
                                                        "{id: 116.pbs, state: F, exit: 0, verified: \"x@t\"}")
               + "```sim-tracker\nid: t-prov\nid: t-prov\nhost: nurion\n```\n")
    (nc / "2026-10-29.md").write_text(nc_text)
    cf_text = blk("t-conf", "104.pbs", ok)
    (cf / "2026-10-01.md").write_text(cf_text)
    link = r"[[10_Projects/10_Active/P/10_ResearchNotes/01_NotConfirmed/2026-10-29\|2026-10-29]]"
    rows = "".join(f"| {k} | {link} | nurion | pbs |\n"
                   for k in ("t-auto", "t-fail", "t-nr", "t-badapp", "t-arch", "t-prov", "t-count", "t-ledger",
                             "t-dup", "t-miss", "t-cfail", "t-pend", "t-badrx", "t-zero", "t-ctl", "t-allbad"))
    rows += f"| t-snr | {link} | della | slurm |\n"
    rows += f"| t-static | {link} | della | pbs |\n"
    rows += f"| t-judge | {link} | della | slurm |\n| t-ghost | {link} | nurion | pbs |\n"
    rows += "| t-path | [[10_Projects/10_Active/P/10_ResearchNotes/01_NotConfirmed/2026-10-28\\|x]] | nurion | pbs |\n"
    rows += (r"| t-conf | [[10_Projects/10_Active/P/10_ResearchNotes/02_Confirmed/2026-10-01\|2026-10-01]]"
             " | nurion | pbs |\n")
    reg_text = ("# Simulation Registry\n\n## Active trackers\n\n| tracker_id | note | host | scheduler |\n"
                "|---|---|---|---|\n" + rows + "\n## Closed since\n\n| tracker_id | note | closed | evidence |\n"
                "|---|---|---|---|\n| t-old | [[x\\|y]] | verified_success | old |\n"
                f"| t-idx | {link} | verified_success | index only |\n\n## Schema\n\ntext\n")
    (prog / "SimulationRegistry.md").write_text(reg_text)
    purged = {"105"}
    pbs = {"100": "F\n    Exit_status = 0", "101": "F\n    Exit_status = 1", "103": "F\n    Exit_status = 0",
           "104": "F\n    Exit_status = 0", "105": "F\n    Exit_status = 0", "106": "F\n    Exit_status = 0",
           "107": "F\n    Exit_status = 0", "108": "F\n    Exit_status = 0", "109": "F\n    Exit_status = 0",
           "110": "F\n    Exit_status = 0", "111": "F\n    Exit_status = 0", "112": "F\n    Exit_status = 0",
           "113": "F\n    Exit_status = 0", "114": "F\n    Exit_status = 0", "118": "F\n    Exit_status = 0",
           "119": "F\n    Exit_status = 0", "120": "F\n    Exit_status = 0"}

    def fake(h: str, script: str, timeout: int = 0) -> tuple[int, bytes, bytes]:
        ts = b"2026-10-29T10:00:00+0900\n"
        if "SIM_CHECKER_EOF" in script:
            assert h == "nurion-dm" or h == "della", h
            r = subprocess.run(["bash", "-s"], input=script.encode(), capture_output=True)
            return r.returncode, r.stdout, ts + r.stderr + ts
        if "qstat" in script:
            ids = re.findall(r"(\d+)\.pbs", script)
            out = "".join(f"Job Id: {i}.pbs\n    job_state = {pbs[i]}\n" for i in ids if i in pbs and i not in purged)
            err = "".join(f"qstat: Unknown Job Id {i}.pbs\n" for i in ids if i not in pbs or i in purged)
            return 153, out.encode(), ts + err.encode() + ts
        if "squeue" in script:
            return 1, b"", ts + b"slurm_load_jobs error: Invalid job id specified\n" + ts
        return 0, b"500|COMPLETED|0:0|0:0\n500.batch|COMPLETED|0:0|\n", ts + ts

    live, remote = st.LIVE, st.REMOTE
    st.LIVE, st.REMOTE = (lambda h: True), fake
    try:
        purged.clear()  # first run: 105 still in history, so its capture is archived and indexed
        out, rc = process(root, dry=True, poll=True)
        assert (nc / "2026-10-29.md").read_text() == nc_text and not (prog / "sim-evidence").exists(), "dry run wrote"
        assert (prog / "SimulationRegistry.md").read_text() == reg_text, "dry run edited the registry"
        out, rc = process(root, dry=False, poll=True)
        rep = "\n".join(out)
        got = {b.key: b.data for b in st.scan(root, None)[0] if not b.error}
        a = got["t-auto"]
        assert a["closure"]["mode"] == "verified_success" and a["jobs"][0]["verified"].startswith(
            "qstat-xf+outputs+logs@"), rep
        assert "outputs 2/2; logs 1/1, 0 reject hits" in a["closure"]["reason"], a["closure"]
        assert "closure" not in got["t-fail"] and "t-fail (01_NotConfirmed/2026-10-29.md" in rep and "FAILED" in rep
        assert "closure" not in got["t-badapp"] and "APP CHECK FAILED" in rep and "bad.log" in rep, rep
        assert "closure" not in got["t-judge"] and "Require X." in rep and "accept t-judge" in rep, rep
        assert "closure" not in got["t-arch"], "judgment item must block auto-closure"
        assert "ORPHAN_NOTE" in rep and "closure" not in got["t-orphan"], rep
        assert "PATH_MISMATCH" in rep and "closure" not in got["t-path"], "path drift must block closure"
        assert "PROVISIONAL_SCHEMA_ERROR" in rep and "t-prov" in rep and "ORPHAN_REGISTRY" in rep, rep
        assert "expected exactly 3" in rep and "closure" not in got["t-count"], "count mismatch passed"
        assert "declared paths missing" in rep and "closure" not in got["t-miss"], "missing declared path passed"
        assert "115.pbs FAIL (cached" in rep, "a purged job with a cached failure must report FAILED"
        assert got["t-allver"]["closure"]["reason"] == "1/1 jobs were already verified", got["t-allver"]
        assert "| t-allver |" in (prog / "SimulationRegistry.md").read_text().split("## Closed since")[1], \
            "a newly closed tracker without any row needs a Closed row"
        assert "closure" not in got["t-allbad"], "an all-verified block closed without its declared checks"
        assert "closure" not in got["t-zero"] and "count must be an integer >= 1" in rep, "vacuous count passed"
        ctl = [x for x in out if x.startswith("t-ctl (") and "JUDGMENT" in x]
        assert "closure" not in got["t-ctl"] and ctl and "controller/ledger" in ctl[0], ctl
        assert "closure" not in got["t-badrx"] and "malformed declared checks" in rep
        assert "controller/ledger" in rep and "closure" not in got["t-ledger"], "ledger gate skipped"
        assert "DUPLICATE_TRACKER_ID" in rep and "STATIC_MISMATCH" in rep and "INDEX_ONLY_CLOSURE" in rep, rep
        assert all("closure" not in got[k] for k in ("t-static", "t-idx")), "drift must block closure"
        partial = st.Obs({"state": "COMPLETED", "exit": "0:0", "derived_exit": "0:0", "tasks_missing": 1}, "FOUND")
        assert _sched_gate(partial, "slurm") == "UNRESOLVED", "partial array coverage passed"
        assert (cf / "2026-10-01.md").read_text() == cf_text and "t-conf (02_Confirmed" in rep and "PROPOSED" in rep
        reg = (prog / "SimulationRegistry.md").read_text()
        act, closed_tab = reg.split("## Closed since")
        assert "| t-auto |" not in act and "| t-auto |" in closed_tab and "verified_success" in closed_tab, reg
        assert "| t-conf |" in act and "| t-fail |" in act, "unclosed rows must stay Active"
        assert "| t-pend |" not in act and "| t-pend |" in closed_tab, "interrupted registry move not completed"
        assert reg.count("## Schema") == 1 and closed_tab.index("| t-auto |") < closed_tab.index("## Schema")

        purged.update({"102", "105"})  # history purged for the next calls
        out, rc = process(root, False, True, only="t-nr")
        assert rc == 1 and "Gmail" in "\n".join(out), out
        mail = tmp / "mail.json"
        mail.write_text(json.dumps([{"id": "102.pbs", "exit_status": 0, "message": "msg-1"}]))
        bad_mail = Ack(mail={"102": 0, "501": 0}, mail_raw=b"[]")  # wrong spelling of 102.pbs; Slurm job
        assert process(root, False, True, only="t-nr", ack=bad_mail)[1] == 1, "mail matched a non-exact ID"
        assert process(root, False, True, only="t-snr", ack=bad_mail)[1] == 1, "mail accepted for a Slurm job"
        assert process(root, False, True, only="t-badrx", ack=Ack(checks="looked fine"))[1] == 1, "probs waived"
        out, rc = process(root, False, True, only="t-nr", ack=Ack(mail=_mail(str(mail)), mail_raw=mail.read_bytes()))
        nr = {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-nr"]
        assert rc == 0 and nr["closure"]["mode"] == "verified_success" and "gmail:" in nr["jobs"][0]["verified"], out
        cap = nr["jobs"][0]["verified"].split("gmail:")[1].split("+")[0]
        assert (root / cap).exists() and (root / cap).with_suffix(".stdout").read_bytes() == mail.read_bytes()

        out, rc = process(root, False, True, only="t-judge", ack=Ack(checks="checked X"))
        j = {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-judge"]
        assert j["jobs"][0]["verified"].startswith("sacct+outputs+logs+checks@"), j["jobs"][0]
        assert "checked X" in j["closure"]["reason"], j["closure"]

        out, rc = process(root, False, True, only="t-arch", ack=Ack(checks="no rerun; outputs ours"))
        assert rc == 1 and "closure" not in {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-arch"]
        caps = [f for f in (prog / "sim-evidence").rglob("*qstat*.stdout") if b"Job Id: 105.pbs" in f.read_bytes()]
        orig = {f: f.read_bytes() for f in caps}
        for f in caps:
            f.write_bytes(orig[f] + b"# appended\n")  # record still reads F/0; only the hash changes
        out, rc = process(root, False, True, only="t-arch", ack=Ack(checks="no rerun; outputs ours", archived=True))
        assert rc == 1, "a capture whose hash no longer matches its index entry was accepted"
        for f in caps:
            f.write_bytes(orig[f])
        metas = {f.with_suffix(".json"): f.with_suffix(".json").read_text() for f in caps}
        for f, m in metas.items():
            f.write_text(m.replace('"endpoint": "nurion"', '"endpoint": "neuron"'))
        out, rc = process(root, False, True, only="t-arch", ack=Ack(checks="no rerun; outputs ours", archived=True))
        assert rc == 1, "an archived capture from another endpoint was accepted"
        for f, m in metas.items():
            f.write_text(m.replace('"exit_status": 153', '"exit_status": 124'))
        out, rc = process(root, False, True, only="t-arch", ack=Ack(checks="no rerun; outputs ours", archived=True))
        assert rc == 1, "a timed-out archived capture was accepted"
        for f, m in metas.items():
            f.write_text(m)
        out, rc = process(root, False, True, only="t-arch", ack=Ack(checks="no rerun; outputs ours", archived=True))
        ar = {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-arch"]
        assert ar["jobs"][0]["verified"].startswith("archived:20_Progress/sim-evidence/"), (out, ar["jobs"][0])

        out, rc = process(root, False, True, only="t-ledger", ack=Ack(checks="ok"))
        assert rc == 1, "ledger closed without --ledger-checked"
        out, rc = process(root, False, True, only="t-ledger", ack=Ack(checks="ok", ledger=True))
        assert {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-ledger"]["closure"], out
        out, rc = process(root, False, False, only="t-orphan", register=True)
        assert rc == 0 and "| t-orphan |" in (prog / "SimulationRegistry.md").read_text().split("## Closed")[0], out
        assert "ORPHAN_NOTE" not in "\n".join(process(root, True, False)[0])

        out, rc = process(root, False, False, only="t-fail", override={"by": "owner", "reason": "abandoned"})
        f = {b.key: b.data for b in st.scan(root, None)[0] if not b.error}["t-fail"]
        assert f["closure"]["mode"] == "owner_override" and f["closure"]["reason"] == "abandoned", out
        assert "owner_override" in (prog / "SimulationRegistry.md").read_text().split("## Closed")[1]
        assert process(root, False, False, only="t-path", override={"by": "o", "reason": "r"})[1] == 1, "drift"
        crlf = nc / "2026-10-30.md"
        crlf.write_bytes(blk("t-crlf", "122.pbs", ok).replace("\n", "\r\n").encode())
        assert process(root, False, False, only="t-crlf", register=True)[1] == 0
        assert process(root, False, False, only="t-crlf", override={"by": "o", "reason": "r"})[1] == 1, \
            "a refused write reported success"
    finally:
        st.LIVE, st.REMOTE = live, remote
    print("selftest ok")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; see the module docstring for usage and exit status."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--selftest"]:
        selftest()
        return 0
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "run")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cmds = {n: sub.add_parser(n) for n in ("run", "accept", "override", "register")}
    for n, p in cmds.items():
        g = p.add_mutually_exclusive_group()
        g.add_argument("--project")
        g.add_argument("--root", type=Path)
        g.add_argument("--all", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        if n != "run":
            p.add_argument("key")
    cmds["run"].add_argument("--no-poll", action="store_true")
    cmds["accept"].add_argument("--checks", required=True, help="what you checked, in one or two sentences")
    cmds["accept"].add_argument("--mail", help="JSON list of exact-ID PBS end-mail records")
    cmds["accept"].add_argument("--archived", action="store_true")
    cmds["accept"].add_argument("--ledger-checked", action="store_true")
    cmds["override"].add_argument("--by", required=True)
    cmds["override"].add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    a.note = None
    roots = st._roots(a)
    key = getattr(a, "key", None)
    if key:
        roots = [r for r in roots if any(not b.error and b.key == key for b in st.scan(r, None)[0])]
        if len(roots) != 1:
            print(f"{key}: found in {len(roots)} projects; pass --project or --root", file=sys.stderr)
            return 1
    if a.cmd == "accept" and not a.checks.strip():
        print("--checks must say what was checked", file=sys.stderr)
        return 1
    if a.cmd == "override" and not (a.by.strip() and a.reason.strip()):
        print("--by and --reason must be nonempty", file=sys.stderr)
        return 1
    ack = Ack(a.checks.strip(), _mail(a.mail) if a.mail else {}, Path(a.mail).read_bytes() if a.mail else b"",
              a.archived, a.ledger_checked) if a.cmd == "accept" else None
    rc = 0
    for r in roots:
        lines, code = process(r, a.dry_run, not getattr(a, "no_poll", False), key, ack,
                              {"by": a.by.strip(), "reason": a.reason.strip()} if a.cmd == "override" else None,
                              a.cmd == "register")
        if lines:
            print(f"== {r.name}\n" + "\n".join(f"  {x}" for x in lines))
        rc = max(rc, code)
    return rc


if __name__ == "__main__":
    sys.exit(main())
