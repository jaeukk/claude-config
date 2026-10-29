#!/usr/bin/env python3
"""Mechanical status poll for sim-tracker blocks: PBS on Nurion, Slurm on Neuron/Della, local runs.

Usage: simtrack.py [--project NAME | --root DIR | --all] [--note YYYY-MM-DD] [--dry-run]
       simtrack.py --selftest

Reads every fenced sim-tracker block under <root>/10_ResearchNotes/{01_NotConfirmed,02_Confirmed},
polls the unverified jobs of every valid, unclosed block with one bounded batched query per
endpoint, saves raw captures in <root>/20_Progress/sim-evidence/<date>/, indexes them in
<root>/20_Progress/SimulationEvidence_<date>.md, and rewrites only the changed job items of
blocks in 01_NotConfirmed. It never verifies, closes, moves notes, submits jobs, or touches
SimulationRegistry.md; that is sim-update's job.

Exit status: 0 done; 1 notes root unresolved; 3 an SSH endpoint was unavailable or wedged.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FIND_ROOT = Path(__file__).resolve().parents[2] / "research-note" / "scripts" / "find_root.py"
# (host, scheduler) -> SSH alias for scheduler queries; None means local /proc and receipt checks.
ENDPOINTS = {("nurion", "pbs"): "nurion", ("neuron", "slurm"): "neuron", ("della", "slurm"): "della",
             ("office-desktop", "local"): None, ("laptop", "local"): None}
SLURM_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL",
                  "PREEMPTED", "BOOT_FAIL", "DEADLINE"}
WRITABLE = {"FOUND", "NOT_FOUND", "UNKNOWN"}  # results recorded in last_query; others are report-only
FENCE = re.compile(r"^```sim-tracker[ \t]*\r?\n(.*?)^```[ \t]*\r?$", re.S | re.M)
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:?\d\d")  # Nurion prints +0900
JOB_ID = {"pbs": re.compile(r"\d+(\.[\w.-]+)?"), "slurm": re.compile(r"\d+(_\d+)?")}  # also guards the remote shell


class StrictLoader(yaml.SafeLoader):
    """YAML 1.1 safe loader that rejects duplicate mapping keys."""


def _strict_map(loader: StrictLoader, node: yaml.MappingNode) -> dict:
    """Construct a mapping, raising on a repeated key."""
    loader.flatten_mapping(node)
    out = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=True)
        if key in out:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", k.start_mark)
        out[key] = loader.construct_object(v, deep=True)
    return out


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_map)


class QuotedDumper(yaml.SafeDumper):
    """Dumper that double-quotes every string, for blocks written in that style."""


QuotedDumper.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style='"'))


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read(p: Path) -> str:
    """Note text with its line endings untouched."""
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


@dataclass
class Block:
    """One sim-tracker fence: location, raw body, and parsed data or schema error."""

    note: Path
    line: int  # 1-based line of the opening fence
    start: int  # body offsets in the note text
    end: int
    body: str
    data: dict | None = None
    error: str = ""

    @property
    def sha256(self) -> str:
        return _sha(self.body.replace("\r\n", "\n").encode())

    @property
    def key(self) -> str:
        return str(self.data.get("id", self.data.get("tracker_id")))

    @property
    def where(self) -> str:
        return f"{self.note.parent.name}/{self.note.name}:{self.line}"


@dataclass
class Capture:
    """One saved scheduler (or local) query."""

    host: str
    name: str
    rc: int
    out: str
    err: str
    path: str  # notes-root-relative path of the metadata JSON
    sha: str  # SHA-256 of raw stdout


@dataclass
class Obs:
    """A fresh observation of one job: field updates, query result, failure flag."""

    upd: dict
    result: str
    failed: bool = False
    note: str = ""
    caps: list[Capture] = field(default_factory=list)


def parse(body: str) -> tuple[dict | None, str]:
    """Strictly parse a tracker body and check the fields polling depends on.

    Parameters
    ----------
    body : str
        Text between the fence lines.

    Returns
    -------
    (dict or None, str)
        The parsed mapping and "" on success, else None and a one-line schema error.
    """
    try:
        d = yaml.load(body, Loader=StrictLoader)
    except (yaml.YAMLError, TypeError) as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1} col {mark.column + 1}: " if mark else ""
        return None, f"YAML {where}{getattr(e, 'problem', None) or e}"
    if not isinstance(d, dict):
        return None, "not a mapping"
    ids = {str(d[k]) for k in ("id", "tracker_id") if k in d}
    if not ids:
        return None, "no id or tracker_id"
    if len(ids) > 1:
        return None, "CONFLICTING_ID_FIELDS"
    if "host" not in d or "scheduler" not in d:
        return None, "missing host or scheduler"
    if (str(d["host"]), str(d["scheduler"])) not in ENDPOINTS:
        return None, f"unsupported host/scheduler {d['host']}/{d['scheduler']}"
    jobs = d.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(j, dict) and "id" in j for j in jobs):
        return None, "jobs is not a list of maps with id"
    seq = next((v for k, v in yaml.compose(body, Loader=StrictLoader).value if k.value == "jobs"), None)
    if not isinstance(seq, yaml.SequenceNode):
        return None, "jobs comes from a YAML merge or alias; write it out"
    for item, j in zip(seq.value, jobs, strict=True):  # e.g. unquoted 12345678_3 loads as 123456783
        raw = next((v for k, v in item.value if k.value == "id"), None)
        if not isinstance(raw, yaml.ScalarNode) or raw.value != str(j["id"]):
            return None, f"job ID {getattr(raw, 'value', '?')!r} loads as {j['id']!r} under YAML 1.1; quote it"
    return d, ""


def scan(root: Path, note: str | None) -> tuple[list[Block], dict[Path, str]]:
    """Find and parse every tracker fence in both research-note folders.

    Parameters
    ----------
    root : Path
        Project notes root (holds 10_ResearchNotes/).
    note : str or None
        Restrict the scan to notes named <note>.md (YYYY-MM-DD).

    Returns
    -------
    (list of Block, dict)
        Blocks in folder/name/offset order, and each scanned note's original text.
    """
    blocks, texts = [], {}
    for sub in ("01_NotConfirmed", "02_Confirmed"):
        for p in sorted((root / "10_ResearchNotes" / sub).glob(f"{note or '*'}.md")):
            texts[p] = text = _read(p)
            for m in FENCE.finditer(text):
                b = Block(p, text.count("\n", 0, m.start()) + 1, m.start(1), m.end(1), m.group(1))
                b.data, b.error = parse(b.body)
                blocks.append(b)
    return blocks, texts


def screen_terms(d: dict) -> list[str]:
    """Tracker key, full and numeric job IDs, and ledger paths to look for in quarantined fences."""
    t = {str(d.get("id", d.get("tracker_id")))}
    for j in d["jobs"]:
        t |= {str(j["id"]), re.split(r"[._\[]", str(j["id"]))[0]}
    ctl = d.get("controller")
    t |= {v for v in (d.get("job_registry"), ctl.get("ledger") if isinstance(ctl, dict) else None)
          if isinstance(v, str)}
    return sorted(x for x in t if x)


def _overlaps(terms: list[str], bad: list[Block]) -> list[str]:
    """Token-boundary hits of terms in quarantined raw fences (array/server suffixes allowed)."""
    return [f"{t} in {b.where}" for t in terms for b in bad
            if re.search(rf"(?<![\w-]){re.escape(t)}(?![A-Za-z0-9-])", b.body)]


def expand(expr: str) -> list[int]:
    """Indices of a Slurm array expression such as '0-24%5' or '1-9:2,12' (throttle ignored)."""
    out: set[int] = set()
    for part in str(expr).split("%")[0].split(","):
        rng, _, step = part.partition(":")
        a, _, b = rng.partition("-")
        out.update(range(int(a), int(b or a) + 1, int(step or 1)))
    return sorted(out)


def _ssh_live(host: str) -> bool:
    try:
        return subprocess.run(["ssh", "-O", "check", host], capture_output=True, timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


# A query rides the user's live master only. If the master dies after the check, the fallback
# direct connection has no authentication method to try, so it fails without any login attempt.
SSH_OPTS = [x for o in ("BatchMode=yes", "ControlMaster=no", "PubkeyAuthentication=no", "PasswordAuthentication=no",
                        "KbdInteractiveAuthentication=no", "GSSAPIAuthentication=no", "HostbasedAuthentication=no")
            for x in ("-o", o)]


def _ssh_run(host: str, script: str, timeout: int = 240) -> tuple[int, bytes, bytes]:
    """Run a bash script on host through the live master only (see SSH_OPTS)."""
    r = subprocess.run(["ssh", *SSH_OPTS, host, "bash", "-s"], input=script.encode(),
                       capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


LIVE, REMOTE = _ssh_live, _ssh_run  # replaced by fakes in the selftest


class Run:
    """Per-root poll state: evidence location, captures, dead endpoints, report lines."""

    def __init__(self, root: Path, dry: bool, label: str = "sim-tracker poll"):
        self.root, self.dry, self.label = root, dry, label
        self.t = dt.datetime.now().astimezone()
        self.rel = f"20_Progress/sim-evidence/{self.t.date()}"
        self.dead: dict[str, str] = {}
        self.lines: list[str] = []
        self.indexed = 0
        self.used: set[str] = set()

    def save(self, host: str, name: str, command: str, ids: list[str], rc: int,
             out: bytes, err: bytes, t0: str) -> Capture:
        """Write raw stdout/stderr plus metadata, then index the capture at once (collection time)."""
        base = f"{self.t:%H%M%S}-{host}-{name}"
        d = self.root / self.rel
        n = 1
        while base in self.used or any((d / f"{base}{x}").exists() for x in (".json", ".stdout", ".stderr")):
            n += 1
            base = f"{self.t:%H%M%S}-{host}-{name}-{n}"
        self.used.add(base)
        c = Capture(host, name, rc, out.decode(errors="replace"), err.decode(errors="replace"),
                    f"{self.rel}/{base}.json", _sha(out))
        stamps = STAMP.findall(c.err)
        meta = {"endpoint": host, "command": command, "ids": ids, "exit_status": rc,
                "local_start": t0, "local_end": _now(),
                "endpoint_start": stamps[0] if stamps else None,
                "endpoint_end": stamps[-1] if len(stamps) > 1 else None,
                "stdout": f"{base}.stdout", "stdout_sha256": c.sha,
                "stderr": f"{base}.stderr", "stderr_sha256": _sha(err)}
        if not self.dry:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{base}.stdout").write_bytes(out)
            (d / f"{base}.stderr").write_bytes(err)
            (d / f"{base}.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        span = f"endpoint {meta['endpoint_start']} to {meta['endpoint_end']}" if stamps else f"local {t0}"
        line = (f"- `{c.path}`: {host} {name}; IDs {', '.join(ids) or '-'}; {span}; "
                f"command exit {rc}; stdout SHA-256 `{c.sha}`; stderr SHA-256 `{meta['stderr_sha256']}`.\n")
        if not self.dry:
            idx = self.root / "20_Progress" / f"SimulationEvidence_{self.t.date()}.md"
            head = "" if idx.exists() else f"# Simulation evidence — {self.t.date()}\n"
            head += "" if self.indexed else f"\n## {self.label} {self.t:%H:%M:%S%z}\n\n"
            with open(idx, "a", encoding="utf-8") as f:
                f.write(head + line)
            self.indexed += 1
        return c

    def query(self, host: str, name: str, cmd: str, ids: list[str], limit: int = 120) -> Capture | None:
        """One bounded remote query bracketed by endpoint timestamps; None if the endpoint failed."""
        script = f"date -Is >&2\ntimeout {limit} {cmd}\nrc=$?\ndate -Is >&2\nexit $rc\n"
        t0 = _now()
        try:
            rc, out, err = REMOTE(host, script, timeout=limit + 120)
        except subprocess.TimeoutExpired:
            self.dead[host] = f"query timed out; the master may be wedged (`ssh -O exit {host}`, then reopen)"
            return None
        if rc == 255:
            self.dead[host] = "ssh failed (exit 255)"
            return None
        c = self.save(host, name, script, ids, rc, out, err, t0)
        saved = " (dry run, not saved)" if self.dry else ""
        self.lines.append(f"{host} {name}: exit {rc}, evidence {c.path}{saved}")
        return c


def _ids(sched: str, items: list, run: Run) -> list[str]:
    """Distinct job IDs to query; rejects malformed IDs before they reach a remote shell."""
    ok = set()
    for b, _, j in items:
        s = str(j["id"])
        if JOB_ID[sched].fullmatch(s):
            ok.add(s)
        else:
            run.lines.append(f"{b.key}: job ID {s!r} is malformed; not queried")
    return sorted(ok)


def qstat_records(out: str) -> dict[str, dict]:
    """`qstat -xf` text -> {numeric job ID: {job_state, Exit_status}}; other lines (banners) ignored."""
    found, cur = {}, None
    for line in out.splitlines():
        if m := re.match(r"Job Id:\s*(\S+)", line):
            cur = found.setdefault(m.group(1).split(".")[0], {})
        elif cur is not None and (m := re.match(r"\s+(job_state|Exit_status) = (\S+)", line)):
            cur[m.group(1)] = m.group(2)
    return found


def sacct_rows(out: str) -> dict[str, tuple[str, str, str]]:
    """`sacct -P` text -> {allocation JobID: (State, ExitCode, DerivedExitCode)}; step rows dropped."""
    acct = {}
    for line in out.splitlines():
        f = line.split("|")
        if len(f) >= 4 and "." not in f[0]:
            acct[f[0]] = (f[1].split()[0] if f[1].strip() else "", f[2], f[3])
    return acct


def poll_pbs(run: Run, host: str, items: list) -> dict:
    """Batched `qstat -xf` for every selected PBS job; returns {(block, index): Obs}."""
    ids = _ids("pbs", items, run)
    c = run.query(host, "qstat", "qstat -xf " + " ".join(ids), ids) if ids else None
    if c is None:
        return {}
    found = qstat_records(c.out)
    unknown = {x.split(".")[0] for x in re.findall(r"Unknown Job Id:?\s+(\S+)", c.err)}
    obs = {}
    for b, i, j in items:
        k = str(j["id"]).split(".")[0]
        st, ex = found.get(k, {}).get("job_state"), found.get(k, {}).get("Exit_status")
        if st == "F" and ex and re.fullmatch(r"-?\d+", ex):
            o = Obs({"state": "F", "exit": int(ex)}, "FOUND", int(ex) != 0)
        elif st == "F":
            o = Obs({"state": "F"}, "FOUND", note="F without numeric Exit_status")
        elif st:
            o = Obs({"state": st, "exit": "-"}, "FOUND")
        elif k in unknown:
            o = Obs({}, "NOT_FOUND")
        else:
            continue
        o.caps = [c]
        obs[(id(b), i)] = o
    return obs


def _outcome(st: str, ex: str, dx: str) -> str:
    """'ok', 'failed' or 'unknown' for a terminal Slurm row; a missing exit field is never success."""
    if st != "COMPLETED" or any(x and x != "0:0" for x in (ex, dx)):
        return "failed"
    return "ok" if ex and dx else "unknown"


def slurm_job(jid: str, array, queue: dict, acct: dict, acct_ok: bool) -> Obs | None:
    """Fold squeue/sacct rows into one job's observation.

    Parameters
    ----------
    jid : str
        Parent job ID.
    array : str or None
        The job row's submitted array expression; None for a plain job.
    queue : dict
        squeue `%i` -> state (task IDs carry `_<index>`); empty if squeue errored.
    acct : dict
        sacct allocation JobID -> (State, ExitCode, DerivedExitCode); step rows excluded.
    acct_ok : bool
        Whether sacct exited 0, the only case in which absence means NOT_FOUND.

    Returns
    -------
    Obs or None
        None when nothing was observed. An array parent gets a terminal state only when every
        expected index has its own sacct row; exit fields are set only for terminal states.
    """
    if array is None:
        if jid in acct:
            st, ex, dx = acct[jid]
            if st not in SLURM_TERMINAL:
                return Obs({"state": st, "exit": "-", "derived_exit": "-"}, "FOUND")
            return Obs({"state": st, "exit": ex or "-", "derived_exit": dx or "-"}, "FOUND",
                       _outcome(st, ex, dx) == "failed", "" if ex and dx else "accounting exit fields missing")
        if jid in queue:
            return Obs({"state": queue[jid], "exit": "-", "derived_exit": "-"}, "FOUND")
        if any(k.startswith(jid + "_") for k in (*acct, *queue)):
            return Obs({}, "ARRAY_EXPRESSION_MISSING", note="array rows returned but the job row has no array:")
        return Obs({}, "NOT_FOUND") if acct_ok else None
    try:
        expected = expand(array)
    except ValueError:
        return Obs({}, "ARRAY_EXPRESSION_INVALID", note=f"cannot expand array {array!r}")
    grouped = {x: v for k, v in acct.items() if (m := re.fullmatch(rf"{re.escape(jid)}_\[(.+)\]", k))
               for x in expand(m.group(1))}
    tasks = {}
    for x in expected:
        k = f"{jid}_{x}"
        tasks[x] = ((*acct[k], "sacct") if k in acct else (grouped[x][0], "", "", "sacct-grouped") if x in grouped
                    else (queue[k], "", "", "squeue") if k in queue else ("UNKNOWN", "", "", "none"))
    if all(t[3] == "none" for t in tasks.values()):
        return Obs({}, "NOT_FOUND") if acct_ok else None
    n = dict.fromkeys(("completed", "failed", "running", "pending", "unknown"), 0)
    first_fail = None
    for st, ex, dx, src in tasks.values():
        if src == "none":
            n["unknown"] += 1
        elif st in SLURM_TERMINAL:
            out = _outcome(st, ex, dx)
            n[{"ok": "completed", "failed": "failed", "unknown": "unknown"}[out]] += 1
            if out == "failed" and st != "COMPLETED":
                first_fail = first_fail or st
        else:
            n["pending" if st == "PENDING" or st.startswith("REQUEUE") else "running"] += 1
    missing = sum(t[3] != "sacct" for t in tasks.values())
    st = ("RUNNING" if n["running"] else "PENDING" if n["pending"] else None if n["unknown"] or missing
          else first_fail or "COMPLETED")
    upd = {"tasks_expected": len(expected), "tasks_missing": missing, **{f"tasks_{k}": v for k, v in n.items()},
           "task_groups": _groups(expected, tasks)}
    if st:
        done = "0:0" if st == "COMPLETED" and not n["failed"] else "-"
        upd |= {"state": st, "exit": done, "derived_exit": done}
    return Obs(upd, "FOUND", n["failed"] > 0, "" if st else "coverage incomplete; parent state left unchanged")


def _groups(expected: list[int], tasks: dict) -> list[dict]:
    """Runs of consecutive indices sharing state, exits (terminal only) and source."""
    runs: list[tuple[list[int], dict]] = []
    for x in expected:
        st, ex, dx, src = tasks[x]
        g = {"state": st, **({"exit": ex, "derived_exit": dx} if st in SLURM_TERMINAL else {}), "source": src}
        if runs and runs[-1][1] == g and runs[-1][0][1] == x - 1:
            runs[-1][0][1] = x
        else:
            runs.append(([x, x], g))
    return [{"indices": f"{a}" if a == b else f"{a}-{b}", **g} for (a, b), g in runs]


def poll_slurm(run: Run, host: str, items: list) -> dict:
    """Batched `squeue -r` plus `sacct --array` for every selected Slurm job."""
    ids = _ids("slurm", items, run)
    if not ids:
        return {}
    j = ",".join(ids)
    sq = run.query(host, "squeue", f"squeue -h -r -j {j} -o '%i|%T'", ids)
    if sq is None:
        return {}
    fields = "-o JobID,State,ExitCode,DerivedExitCode"
    sa = run.query(host, "sacct", f"sacct --array -P -n -j {j} {fields}", ids)
    if sa is not None and sa.rc and re.search(r"unrecognized option|invalid option", sa.err):
        sa = run.query(host, "sacct-noarray", f"sacct -P -n -j {j} {fields}", ids)
    if sa is None:
        return {}
    queue = dict(x.split("|", 1) for x in sq.out.splitlines() if "|" in x) if sq.rc == 0 else {}
    acct = sacct_rows(sa.out)
    obs = {}
    for b, i, job in items:
        if JOB_ID["slurm"].fullmatch(str(job["id"])) and (o := slurm_job(
                str(job["id"]), job.get("array"), queue, acct, sa.rc == 0)):
            o.caps = [sq, sa]
            obs[(id(b), i)] = o
    return obs


def _start_ticks(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat (the 20th token after the last ')'), or None if gone."""
    try:
        s = Path(f"/proc/{pid}/stat").read_text()
        return int(s[s.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def check_receipt(path: Path, d: dict, j: dict) -> tuple[bool, str, int | None]:
    """Validate a local wrapper's final receipt against the block's launch record.

    Parameters
    ----------
    path : Path
        The job's recorded absolute receipt path.
    d, j : dict
        The tracker block and the job row (run ID, pid, boot_id, start_ticks).

    Returns
    -------
    (bool, str, int or None)
        Validity, a reason, and the integer exit_code when valid.
    """
    if str(j["id"]) not in path.name:
        return False, "receipt filename lacks the run ID", None
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "process gone and no receipt", None
    except (OSError, ValueError) as e:
        return False, f"unreadable receipt: {e}", None
    if not isinstance(r, dict):
        return False, "receipt is not a JSON object", None
    want = {"schema": 1, "run_id": str(j["id"]), "host": d["host"], "machine_id": str(d["machine"]["machine_id"]),
            "boot_id": str(j["boot_id"]), "pid": int(j["pid"]), "start_ticks": int(j["start_ticks"])}
    if bad := [k for k, v in want.items() if r.get(k) != v]:
        return False, f"receipt does not match the launch record: {', '.join(bad)}", None
    try:
        t0, t1 = (dt.datetime.fromisoformat(r[k]) for k in ("started_at", "finished_at"))
    except (KeyError, TypeError, ValueError):
        return False, "receipt timestamps missing or invalid", None
    if t0.tzinfo is None or t1.tzinfo is None or t1 < t0:
        return False, "receipt timestamps not timezone-aware or out of order", None
    code = r.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool):
        return False, "receipt exit_code is not an integer", None
    return True, "valid receipt", code


def poll_local(run: Run, items: list) -> dict:
    """Process-identity and receipt checks for local runs, only on the execution machine itself."""
    host = socket.gethostname()
    try:
        mid = Path("/etc/machine-id").read_text().strip()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        run.lines.append("local: no /etc/machine-id or boot_id here; local blocks not polled")
        return {}
    obs, facts, t0 = {}, [], _now()
    for b, i, j in items:
        m = b.data.get("machine") if isinstance(b.data.get("machine"), dict) else {}
        if m.get("hostname") != host or str(m.get("machine_id")) != mid:
            run.lines.append(f"{b.key}: runs on {m.get('hostname')}, not this machine ({host}); "
                             "run the poll on the execution machine")
            continue
        if need := [k for k in ("pid", "boot_id", "start_ticks", "receipt") if k not in j]:
            run.lines.append(f"{b.key} {j['id']}: launch record lacks {', '.join(need)}; not polled")
            continue
        try:
            pid, want_ticks = int(j["pid"]), int(j["start_ticks"])
        except (TypeError, ValueError):
            run.lines.append(f"{b.key} {j['id']}: pid/start_ticks are not integers; not polled")
            continue
        ticks = _start_ticks(pid)
        fact = {"job": str(j["id"]), "pid": j["pid"], "boot_id": boot, "proc_start_ticks": ticks}
        if boot == str(j["boot_id"]) and ticks == want_ticks:
            o = Obs({"state": "RUNNING", "exit": "-"}, "FOUND")
        else:
            ok, why, code = check_receipt(Path(j["receipt"]), b.data, j)
            fact |= {"receipt": str(j["receipt"]), "receipt_valid": ok, "reason": why}
            o = Obs({"state": "EXITED", "exit": code}, "FOUND", code != 0) if ok else Obs({}, "UNKNOWN", note=why)
        facts.append(fact)
        obs[(id(b), i)] = o
    if facts:
        c = run.save(host, "local", "local /proc identity and receipt check", [f["job"] for f in facts], 0,
                     json.dumps(facts, indent=1).encode(), b"", t0)
        for o in obs.values():
            o.caps = [c]
    return obs


def _terminal(j: dict, sched: str) -> bool:
    s, e = str(j.get("state")), j.get("exit")
    if sched == "pbs":
        return s == "F" and isinstance(e, int)
    if sched == "slurm":
        return s in SLURM_TERMINAL
    return s == "EXITED" and isinstance(e, int)


def update_block(b: Block, bobs: dict[int, Obs], at: str) -> tuple[dict, list[str], str]:
    """Apply fresh observations to a copy of the block's data.

    Returns
    -------
    (dict, list of str, str)
        New data (the original object if nothing was observed), change messages, and a
        sim-update hint ("" when none).
    """
    new = copy.deepcopy(b.data)
    msgs, nf, observed, failed, caps = [], 0, False, False, {}
    for i, o in sorted(bobs.items()):
        j = new["jobs"][i]
        jid = str(j["id"])
        if o.result not in WRITABLE:
            msgs.append(f"{jid} {o.result}: {o.note}")
            continue
        observed, failed = True, failed or o.failed
        old = f"{j.get('state')}/{j.get('exit')}"
        j.update(o.upd)
        j["last_query"] = {"at": at, "source": " + ".join(f"{c.host} {c.name}" for c in o.caps),
                           "result": o.result, "evidence": [c.path for c in o.caps]}
        caps |= {c.path: c.sha for c in o.caps}
        if o.result == "NOT_FOUND":
            nf += 1
        elif (now := f"{j.get('state')}/{j.get('exit')}") != old:
            msgs.append(f"{jid} {old} -> {now}")
        if o.note:
            msgs.append(f"{jid} {o.result}: {o.note}")
    if nf:
        msgs.append(f"{nf} NOT_FOUND (cached state kept)")
    if not observed:
        return b.data, msgs, ""
    new["last_polled"] = at
    new["last_poll_evidence"] = [{"path": p, "stdout_sha256": s} for p, s in caps.items()]
    todo = [j for j in new["jobs"] if not j.get("verified")]
    done = all(_terminal(j, b.data["scheduler"]) for j in todo)
    if failed or new.get("status") == "failed":
        new["status"] = "failed"
    elif not done:
        new["status"] = "running"
    hint = "failed: sim-update to diagnose" if new.get("status") == "failed" else (
        "all jobs terminal: ready for sim-update verification" if done else "")
    return new, msgs, hint


def _dump_job(job: dict, flow: bool, quoted: bool, col: int) -> str:
    """Re-emit one job item in its original flow/block and quoting style."""
    D = QuotedDumper if quoted else yaml.SafeDumper
    kw = dict(Dumper=D, sort_keys=False, allow_unicode=True)
    if flow:
        return yaml.dump(job, default_flow_style=True, width=10**9, **kw).strip()
    return ("\n" + " " * col).join(yaml.dump(job, default_flow_style=False, width=120, **kw).rstrip("\n").split("\n"))


def rewrite(body: str, old: dict, new: dict) -> str:
    """Splice changed jobs and top-level values into a tracker body, leaving all else byte-identical.

    Only job items that differ are re-emitted; changed top-level values are written as JSON
    (valid YAML flow, so YAML 1.1 never coerces them) in place, or appended if new.
    """
    root = yaml.compose(body, Loader=StrictLoader)
    top = {k.value: v for k, v in root.value}
    edits = [(it.start_mark.index, it.end_mark.index,
              _dump_job(b, it.flow_style, it.value[0][0].style == '"', it.start_mark.column))
             for it, a, b in zip(top["jobs"].value, old["jobs"], new["jobs"], strict=True) if a != b]
    quoted, tail = root.value[0][0].style == '"', ""
    for k, v in new.items():
        if k == "jobs" or (k in old and v == old[k]):
            continue
        val = json.dumps(v, ensure_ascii=False)
        if k in top:
            edits.append((top[k].start_mark.index, top[k].end_mark.index, val))
        else:
            tail += f"{json.dumps(k) if quoted else k}: {val}\n"
    for s, e, t in sorted(edits, reverse=True):
        orig = body[s:e]
        body = body[:s] + t + orig[len(orig.rstrip()):] + body[e:]
    return body + tail


def stage(b: Block, new: dict, dry: bool) -> tuple[str, str | None]:
    """Verdict for writing new data into a block, and the validated new body when writable."""
    if b.note.parent.name != "01_NotConfirmed":
        return "PROPOSED ONLY (02_Confirmed is never edited)", None
    if dry:
        return "DRY RUN", None
    if "\r" in b.body:
        return "PROPOSED ONLY (CRLF note; not edited)", None
    nb = rewrite(b.body, b.data, new)
    d2, err = parse(nb)
    if err or d2 != new:
        return "VALIDATION FAILED; note left unchanged", None
    return "WRITTEN", nb


def write_notes(texts: dict[Path, str], writes: dict[Path, list]) -> list[str]:
    """Splice staged bodies into their notes; each note is re-checked, then atomically replaced."""
    out = []
    for p, edits in writes.items():
        if _read(p) != texts[p]:
            out.append(f"{p.name}: changed during the run; NOT written, rerun")
            continue
        text = texts[p]
        for b, nb in sorted(edits, key=lambda e: e[0].start, reverse=True):
            text = text[:b.start] + nb + text[b.end:]
        tmp = p.with_name(f".{p.name}.simtrack.tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, p)
        texts[p] = text
    return out


def poll_root(root: Path, note: str | None, dry: bool, fresh: dict | None = None) -> tuple[list[str], bool]:
    """Poll one project's trackers end to end.

    Parameters
    ----------
    root : Path
        Project notes root.
    note : str or None
        Limit to one note (YYYY-MM-DD).
    dry : bool
        Query and report, but write nothing (no evidence, index, or note edits).
    fresh : dict, optional
        Filled with {tracker key: {job index: Obs}} for every job observed in this run.

    Returns
    -------
    (list of str, bool)
        Report lines, and whether any SSH endpoint was unavailable.
    """
    run = Run(root, dry)
    blocks, texts = scan(root, note)
    bad = [b for b in blocks if b.error]
    selected, out = [], []
    for b in blocks:
        if b.error or "closure" in b.data:
            continue
        todo = [(i, j) for i, j in enumerate(b.data["jobs"]) if not j.get("verified")]
        if not b.data["jobs"]:
            out.append(f"{b.key} ({b.where}): NO_JOBS; sim-update reconciles the registration")
        elif not todo:
            out.append(f"{b.key} ({b.where}): every job verified; sim-update closure decision")
        elif hits := _overlaps(screen_terms(b.data), bad):
            out.append(f"{b.key} ({b.where}): EXCLUDED, overlaps quarantined fences ({'; '.join(hits)}); sim-update")
        else:
            selected.append((b, todo))
    if not selected:
        return ([f"quarantined: {len(bad)} fences with schema errors"] if bad else []) + out, False
    if bad:
        facts = {"quarantined": [{"note": b.where, "sha256": b.sha256, "error": b.error} for b in bad],
                 "terms": {b.key: screen_terms(b.data) for b, _ in selected}, "hits": []}
        c = run.save("vault", "screen", "raw-text overlap screen of quarantined fences", [], 0,
                     json.dumps(facts, indent=1).encode(), b"", _now())
        out.insert(0, f"quarantined: {len(bad)} fences with schema errors (not polled); "
                      f"no overlap with selected trackers; screen {c.path}")
    groups: dict[tuple[str, str], list] = {}
    for b, todo in selected:
        groups.setdefault((b.data["host"], b.data["scheduler"]), []).extend((b, i, j) for i, j in todo)
    obs: dict = {}
    for (host, sched), items in groups.items():
        alias = ENDPOINTS[(host, sched)]
        if alias is None:
            obs |= poll_local(run, items)
        elif not LIVE(alias):
            run.dead[alias] = "no live master socket"
        else:
            obs |= (poll_pbs if sched == "pbs" else poll_slurm)(run, alias, items)
    at, writes = _now(), {}
    for b, _ in selected:
        bobs = {i: o for (bid, i), o in obs.items() if bid == id(b)}
        if fresh is not None:
            fresh[b.key] = bobs
        new, msgs, hint = update_block(b, bobs, at)
        verdict, nb = ("NOT OBSERVED", None) if new is b.data else stage(b, new, dry)
        if nb is not None:
            writes.setdefault(b.note, []).append((b, nb))
        out.append(f"{b.key} ({b.where}) {verdict}: {'; '.join(msgs) or 'no state change'}"
                   + (f" -> {hint}" if hint else ""))
    out += write_notes(texts, writes)
    out[:0] = run.lines
    out += [f"{h}: UNAVAILABLE ({why}); ask the user to open it (`ssh {h}`, OTP, leave it open); do not retry"
            for h, why in run.dead.items()]
    return out, bool(run.dead)


def _roots(a: argparse.Namespace) -> list[Path]:
    """Notes roots to poll, resolved through research-note's find_root.py."""
    if a.root:
        return [a.root.resolve()]
    if a.all:
        r = subprocess.run([sys.executable, str(FIND_ROOT), "--vault"], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"vault unresolved: {r.stderr.strip()}")
        return sorted(p for p in Path(r.stdout.strip()).glob("10_Projects/*/*") if (p / "10_ResearchNotes").is_dir())
    cmd = [sys.executable, str(FIND_ROOT), "--cwd", os.getcwd()] + (["--project", a.project] if a.project else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"notes root unresolved; rerun with --project NAME or --all\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    return [Path(r.stdout.strip())]


def selftest() -> None:
    """Offline end-to-end check with fake SSH endpoints and a temporary notes root."""
    import tempfile
    global LIVE, REMOTE
    tmp = Path(tempfile.mkdtemp())
    root, nc, cf = tmp / "P", tmp / "P/10_ResearchNotes/01_NotConfirmed", tmp / "P/10_ResearchNotes/02_Confirmed"
    nc.mkdir(parents=True)
    cf.mkdir(parents=True)
    host, mid = socket.gethostname(), Path("/etc/machine-id").read_text().strip()
    boot, ticks = Path("/proc/sys/kernel/random/boot_id").read_text().strip(), _start_ticks(os.getpid())
    done = tmp / "run-done.receipt.json"
    done.write_text(json.dumps({"schema": 1, "run_id": "run-done", "host": "office-desktop", "machine_id": mid,
                                "boot_id": boot, "pid": 999999999, "start_ticks": 1,
                                "started_at": "2026-10-29T09:00:00+09:00", "finished_at": "2026-10-29T09:30:00+09:00",
                                "exit_code": 0}))
    bad_rc = tmp / "run-bad.receipt.json"  # pid differs from its launch record
    bad_rc.write_text(done.read_text().replace('"run-done"', '"run-bad"').replace("999999999", "123"))
    verified ="  - {id: 102.pbs, file: c.sh, state: F, exit: 0, verified: \"x@2026-10-01T00:00:00+09:00\"}\n"
    nc_text = (
        "# note\n\n```sim-tracker\nid: t-pbs\nhost: nurion\nscheduler: pbs\nstatus: running\n"
        "last_polled: 2026-06-09 08:12\njobs:\n  - {id: 100.pbs, file: a.sh, state: R, exit: \"-\"}\n"
        "  - {id: 101.pbs, file: b.sh, state: F, exit: 0}\n" + verified + "```\n\ntext\n\n"
        "```sim-tracker\n\"id\": \"t-slurm\"\n\"host\": \"della\"\n\"scheduler\": \"slurm\"\n\"status\": \"running\"\n"
        "\"jobs\":\n- \"id\": 500\n  \"array\": \"0-3%2\"\n  \"state\": \"PENDING\"\n  \"exit\": \"-\"\n"
        "- \"id\": 501\n  \"state\": \"Q\"\n```\n\n"
        "```sim-tracker\nid: t-bad\nhost: della\nhost: della\nnote: see 777.pbs\njobs: []\n```\n\n"
        "```sim-tracker\nid: t-overlap\nhost: nurion\nscheduler: pbs\nstatus: running\njobs:\n"
        "  - {id: 777.pbs, state: R}\n```\n\n"
        "```sim-tracker\nid: t-dead\nhost: neuron\nscheduler: slurm\nstatus: running\njobs:\n"
        "  - {id: 900, state: RUNNING}\n```\n\n"
        f"```sim-tracker\nid: t-local\nhost: office-desktop\nscheduler: local\n"
        f"machine: {{hostname: {host}, machine_id: \"{mid}\"}}\nstatus: running\njobs:\n"
        f"  - id: run-live\n    state: RUNNING\n    exit: \"-\"\n    pid: {os.getpid()}\n    boot_id: \"{boot}\"\n"
        f"    start_ticks: {ticks}\n    receipt: {tmp}/run-live.receipt.json\n"
        f"  - id: run-done\n    state: RUNNING\n    exit: \"-\"\n    pid: 999999999\n    boot_id: \"{boot}\"\n"
        f"    start_ticks: 1\n    receipt: {done}\n"
        f"  - id: run-bad\n    state: RUNNING\n    exit: \"-\"\n    pid: 999999998\n    boot_id: \"{boot}\"\n"
        f"    start_ticks: 1\n    receipt: {bad_rc}\n```\n")
    cf_text = ("```sim-tracker\nid: t-conf\nhost: nurion\nscheduler: pbs\nstatus: running\njobs:\n"
               "  - {id: 103.pbs, state: R}\n```\n")
    (nc / "2026-10-29.md").write_text(nc_text)
    (cf / "2026-10-01.md").write_text(cf_text)
    scripts: list[str] = []

    def fake(h: str, script: str, timeout: int = 0) -> tuple[int, bytes, bytes]:
        scripts.append(script)
        ts = "2026-10-29T10:00:00+09:00\n"
        if "qstat" in script:
            ts = "2026-10-29T10:00:00+0900\n"  # Nurion's date -Is has no colon
            out = ("=== Account Information ===\n * Due Date : 2027/10/06\n"  # Nurion's login banner is on stdout
                   "Job Id: 100.pbs\n    job_state = F\n    Exit_status = 0\nJob Id: 103.pbs\n    job_state = R\n")
            return 153, out.encode(), (ts + "qstat: Unknown Job Id 101.pbs\n" + ts).encode()
        if "squeue" in script:
            return 0, b"500_3|PENDING\n501|RUNNING\n", (ts + ts).encode()
        out = ("500_0|COMPLETED|0:0|0:0\n500_0.batch|COMPLETED|0:0|0:0\n500_1|COMPLETED|0:0|0:0\n"
               "500_2|FAILED|1:0|0:0\n500_[3]|PENDING|0:0|0:0\n501|RUNNING|0:0|0:0\n")
        return 0, out.encode(), (ts + ts).encode()

    LIVE, REMOTE = (lambda h: h != "neuron"), fake
    try:
        assert expand("0-24%5") == list(range(25)) and expand("1-7:3,10") == [1, 4, 7, 10]
        assert parse("id: a\nid: b\nhost: della\nscheduler: slurm\njobs: []\n")[1].startswith("YAML"), "dup key"
        two = {"7_0": ("COMPLETED", "0:0", "0:0"), "7_1": ("COMPLETED", "0:0", "0:0")}
        assert slurm_job("7", "0-1", {}, two, True).upd["state"] == "COMPLETED"
        part = slurm_job("7", "0-2", {}, two, True)
        assert "state" not in part.upd and part.upd["tasks_unknown"] == 1, "partial coverage is never COMPLETED"
        assert slurm_job("7", "0-1", {}, {}, True).result == "NOT_FOUND"
        masked = slurm_job("7", "0-1", {}, {**two, "7_1": ("COMPLETED", "0:0", "1:0")}, True)
        assert masked.failed and masked.upd["exit"] == "-" and masked.upd["derived_exit"] == "-", "fabricated 0:0"
        blank = slurm_job("7", "0-1", {}, {**two, "7_1": ("COMPLETED", "", "")}, True)
        assert "state" not in blank.upd and blank.upd["tasks_unknown"] == 1, "missing exits counted as success"
        assert slurm_job("7", None, {}, {"7": ("COMPLETED", "", "")}, True).upd["exit"] == "-"
        plain = slurm_job("7", None, {}, {"7": ("FAILED", "1:0", "")}, True)
        assert plain.failed and plain.upd["exit"] == "1:0", "missing derived exit erased a failure"
        arr = slurm_job("7", "0-1", {}, {**two, "7_1": ("FAILED", "1:0", "")}, True)
        assert arr.failed and arr.upd["tasks_failed"] == 1 and arr.upd["state"] == "FAILED", arr.upd
        merged = "base: &b\n  jobs:\n    - {id: \"1\"}\nid: a\nhost: della\nscheduler: slurm\n<<: *b\n"
        assert "merge" in parse(merged)[1], "merged jobs must be quarantined, not crash"
        head = "id: a\nhost: della\nscheduler: slurm\njobs:\n"
        assert "quote it" in parse(head + "  - {id: 12345678_3}\n")[1], "YAML 1.1 coerced task ID accepted"
        assert parse(head + '  - {id: "12345678_3"}\n')[0] and JOB_ID["slurm"].fullmatch("12345678_3")
        assert {f"{m}Authentication=no" for m in ("Pubkey", "Password", "KbdInteractive", "GSSAPI", "Hostbased")} | {
            "BatchMode=yes", "ControlMaster=no"} <= set(SSH_OPTS[1::2]), "a fallback connection could authenticate"
        assert slurm_job("7", "0-1", {}, {}, False) is None, "sacct error is no evidence"

        lines, dead = poll_root(root, None, dry=True)
        assert (nc / "2026-10-29.md").read_text() == nc_text and not (root / "20_Progress").exists(), "dry run wrote"
        lines, dead = poll_root(root, None, dry=False)
        report = "\n".join(lines)
        assert dead and "neuron: UNAVAILABLE" in report, report
        assert not any("102.pbs" in s or "777" in s for s in scripts), "verified or excluded job was queried"
        assert "t-overlap" in report and "EXCLUDED" in report, report
        text = (nc / "2026-10-29.md").read_text()
        assert verified in text, "unchanged job line was rewritten"
        assert "  - {id: 900, state: RUNNING}\n" in text and "  - {id: 777.pbs, state: R}\n" in text
        got = {b.key: b.data for b in scan(root, None)[0] if not b.error}
        pbs, sl, loc = got["t-pbs"], got["t-slurm"], got["t-local"]
        assert pbs["jobs"][0]["state"] == "F" and pbs["jobs"][0]["exit"] == 0, pbs
        nf = pbs["jobs"][1]
        assert (nf["state"], nf["exit"], nf["last_query"]["result"]) == ("F", 0, "NOT_FOUND"), "cached state lost"
        assert pbs["status"] == "running" and isinstance(pbs["last_polled"], str) and STAMP.match(pbs["last_polled"])
        a, b = sl["jobs"]
        assert (a["state"], a["tasks_missing"], a["tasks_failed"], a["tasks_pending"]) == ("PENDING", 1, 1, 1), a
        assert a["task_groups"][0] == {"indices": "0-1", "state": "COMPLETED", "exit": "0:0", "derived_exit": "0:0",
                                       "source": "sacct"}, "0:0 must stay a string"
        assert b["state"] == "RUNNING" and sl["status"] == "failed", sl
        assert [j["state"] for j in loc["jobs"]] == ["RUNNING", "EXITED", "RUNNING"], loc
        assert loc["jobs"][1]["exit"] == 0 and loc["jobs"][2]["last_query"]["result"] == "UNKNOWN", "bad receipt"
        assert (cf / "2026-10-01.md").read_text() == cf_text and "PROPOSED ONLY" in report, "02_Confirmed edited"
        ev = (root / "20_Progress" / f"SimulationEvidence_{dt.date.today()}.md").read_text()
        assert "qstat" in ev and "sacct" in ev and "screen" in ev, ev
        assert "endpoint 2026-10-29T10:00:00+0900 to 2026-10-29T10:00:00+0900" in ev, "Nurion stamps lost"

        def interrupted(h: str, script: str, timeout: int = 0) -> tuple[int, bytes, bytes]:
            if h == "della":
                raise KeyboardInterrupt
            return fake(h, script, timeout)
        REMOTE = interrupted
        try:
            poll_root(root, None, dry=False)
            raise AssertionError("interrupt swallowed")
        except KeyboardInterrupt:
            pass
        ev2 = (root / "20_Progress" / f"SimulationEvidence_{dt.date.today()}.md").read_text()
        assert ev2.count("nurion qstat") == 2, "capture taken before an interrupt was not indexed"
    finally:
        LIVE, REMOTE = _ssh_live, _ssh_run
    print("selftest ok")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; see the module docstring for usage and exit status."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--project", help="project name, as find_root.py --project takes it")
    g.add_argument("--root", type=Path, help="notes root directory")
    g.add_argument("--all", action="store_true", help="every project in the vault")
    ap.add_argument("--note", metavar="YYYY-MM-DD", help="poll only this note's blocks")
    ap.add_argument("--dry-run", action="store_true", help="query and report; write nothing")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return 0
    dead, said = False, False
    for r in _roots(a):
        lines, d = poll_root(r, a.note, a.dry_run)
        dead |= d
        if lines:
            said = True
            print(f"== {r.name}\n" + "\n".join(f"  {x}" for x in lines))
    if not said:
        print("no sim-tracker blocks found")
    return 3 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
