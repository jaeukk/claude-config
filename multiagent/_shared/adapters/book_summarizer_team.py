#!/usr/bin/env python3
"""Run ``book-summarizer`` chapter by chapter as headless team-account workers.

The engine's CLI worker has no Bash, so it cannot render pages, crop figures or run the
vault's gates, and ``dispatch-worker`` therefore cannot execute a book build. This driver is
the sanctioned substitute, generalized from the paper-reviewer driver that built 16 papers
on the team account (``tasks/2026-09-18-plasmon-litsearch-campaign/workers/implementer/
wave3/run_wave3.py``): one ``claude -p --agent book-summarizer`` process per chapter under
``CLAUDE_CONFIG_DIR=~/.claude-team``, cwd = the contract's ``target_repo`` (the vault, so the
vault's agent definition and scripts resolve), and an authorship assertion recorded on the
contract after every built chapter so a later cross-family critic can be dispatched through
the engine once the user records ``authorship_assertion``.

Before a chapter's first attempt the driver harvests the owner's Zotero highlights for the
chapter's pages (and the book's child notes for ``first_chapter``) with the vault's
``99_SYSTEM/scripts/zotero_annotations.py``, runs ``owner-question-responder`` headless when the
harvest lists questions, and passes the files to the worker as ``ANNOTATIONS_FILE``,
``NOTES_FILE`` and ``RESPONSES_FILE``, so they land in the overview's first write. A failed
harvest still yields a file that says so; the worker records "not harvested" instead of omitting
the section. After a chapter is built, the questions the responder could not answer are filed in
the vault's ``40_Resources/99_Queries/Owner_Questions.md`` for a later wiki query.

Unlike that precedent, the shell grant is a named allowlist (the PDF tools, python3 for the
crops and gates, curl for the local Zotero API, and read-only file commands), not bare Bash.
Containment is still the brief only: nothing here intercepts a write. Record that in the
contract's ``deviations``, as the paper-reviewer runs did.

Usage
-----
    python3 book_summarizer_team.py --task-dir <contract dir> --job <job.json>
            [--only 20 21] [--jobs 1] [--timeout-min 120] [--dry-run]

The contract dir holds ``task.yaml`` (``target_repo`` = vault root) and
``workers/implementer/brief-book.md``; results land beside the brief. The job file is
documented in ``_templates/book-summarizer-team/job.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE = ROOT / "engine" / "policy_engine.py"
TEAM_DIR = pathlib.Path.home() / ".claude-team"
LIMIT = re.compile(r"(session|usage|weekly) limit|rate.?limit|429", re.I)
RESET = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)
#: The launcher drops inherited MCP servers, so the worker reads Zotero over the local REST
#: API instead; the user id comes from the `zotero-obsidian-sync` skill, never `/users/0/`.
ZOTERO_USER = 5872032
#: What the agent definition and contract actually invoke: page render and text (pdftoppm,
#: pdftotext, pdfinfo), python3 for crops and the three gate scripts, curl for the local
#: Zotero API, wslpath for attachment paths, plus read-only file commands. No rm, mv, git,
#: pip or network beyond curl.
SHELL_ALLOW = [
    f"Bash({name}:*)" for name in (
        "pdftoppm", "pdftotext", "pdfinfo", "python3", "curl", "wslpath", "mkdir", "cp",
        "convert", "magick", "identify", "cd", "ls", "cat", "head", "tail", "wc", "grep",
        "find", "stat", "file", "md5sum", "date", "echo",
    )
]
#: The responder only renders (through page_images.py) and reads pages, so its grant is narrower.
RESPONDER_ALLOW = [f"Bash({name}:*)" for name in ("python3", "pdftotext", "pdfinfo", "ls", "grep")]
HARVEST = "99_SYSTEM/scripts/zotero_annotations.py"
QUEUE = "40_Resources/99_Queries/Owner_Questions.md"
#: Measured 2026-09-22 (§20.1 smoke, 160 turns): 11 shell calls were denied, all of them
#: shapes a prefix rule cannot match -- a leading `VAR=...`/`export`, a `for` loop, `cd`
#: outside the vault, `bash script.sh`, `chmod` -- and the worker recovered every time. The
#: brief tells it to call the listed tools directly, one per call; keep that line in the brief.

gate = threading.Event()
gate.set()
lock = threading.Lock()


class Driver:
    """One book build: contract, brief, job, log; ``build`` runs one chapter."""

    def __init__(self, task_dir: pathlib.Path, job: dict, timeout_min: int, dry_run: bool) -> None:
        self.task_dir = task_dir
        self.work = task_dir / "workers" / "implementer"
        self.brief = (self.work / "brief-book.md").read_text(encoding="utf-8")
        self.log_path = self.work / "driver.log"
        contract = json.loads((task_dir / "task.yaml").read_text(encoding="utf-8"))
        self.vault = pathlib.Path(contract["target_repo"]).expanduser().resolve()
        self.job = job
        self.model = job.get("model", "claude-sonnet-5-5")
        root = pathlib.Path(job["book_root"])
        self.book_root = root if root.is_absolute() else self.vault / root
        self.timeout = timeout_min * 60
        self.dry_run = dry_run
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(TEAM_DIR)}
        self.owner_fields: dict[str, list[str]] = {}

    def log(self, msg: str) -> None:
        """Append a timestamped line to the driver log and echo it."""
        line = f"[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}"
        with lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        print(line, flush=True)

    def overview(self, chapter: dict) -> pathlib.Path | None:
        """The chapter's ``x.00`` overview note, if one exists."""
        folder = self.book_root / chapter["folder"]
        hits = sorted(folder.glob(f"{chapter['chapter']}.00_*.md")) if folder.is_dir() else []
        return hits[0] if hits else None

    def done(self, chapter: dict) -> bool:
        """True when the overview note exists, is attributed, and is not a stub."""
        note = self.overview(chapter)
        return bool(
            note and note.stat().st_size > 2000
            and re.search(r"^agent:", note.read_text(encoding="utf-8"), re.M)
        )

    def prompt(self, chapter: dict) -> str:
        """The brief plus the per-chapter fields the agent's orchestration section asks for."""
        job = self.job
        key = job.get("zotero_key", "none")
        fields = [
            f"Vault root / cwd: `{self.vault}`",
            f"BUILDER_ID: {self.model}",
            f"citekey: {job['citekey']}; zotero-key: {key}",
            f"PDF (read THIS file; nothing else is the source): `{job['pdf']}`",
            f"book root: `{self.book_root}`; this chapter's folder: "
            f"`{self.book_root / chapter['folder']}` (create if absent)",
            f"page offset: printed_page = PDF_page - {job['offset']}; verify it from a running header",
            f"chapter: {chapter['chapter']}; PDF pages {chapter['pdf_pages']}; "
            f"scope: {chapter.get('scope', 'the whole chapter')}",
            f"focus: {chapter.get('focus', 'none beyond the agent definition')}",
            "allow-list: build it yourself with Glob over the book root; a basename absent there stays plain text",
            "Zotero MCP tools are unavailable in this run: read item metadata with "
            f"`curl -s http://localhost:23119/api/users/{ZOTERO_USER}/items/{key}`; the owner's "
            "notes and highlights come only from the harvest files below",
            f"today: {dt.date.today():%Y-%m-%d}",
            f"RENDER_DIR: `{self.render_dir(chapter)}` (page renders shared with the responder; "
            "render and crop only with 99_SYSTEM/scripts/page_images.py)",
            *self.owner_fields.get(str(chapter["chapter"]),
                                   ["owner annotations: harvested at launch (dry run shows none)"]),
        ]
        return f"{self.brief}\n\n---\n# Per-chapter fields\n" + "\n".join(f"- {f}" for f in fields) + "\n"

    def command(self, chapter: dict) -> list[str]:
        """The headless launch: the vault's agent, the job's model, a named tool allowlist, no MCP."""
        argv = ["claude", "--model", self.model, "--agent", "book-summarizer", "-p", self.prompt(chapter),
                "--output-format", "json", "--permission-mode", "acceptEdits",
                "--allowedTools", *SHELL_ALLOW, "Read", "Write", "Edit", "Glob", "Grep",
                "--strict-mcp-config"]
        if self.vault not in self.book_root.parents:
            # An isolated book root (a smoke test, a scratch copy) is outside cwd, so the
            # worker's file tools need it granted explicitly; `acceptEdits` covers cwd only.
            argv += ["--add-dir", str(self.book_root)]
        return argv

    def record_author(self, chapter: dict) -> None:
        """Assert Claude authorship on the contract; the engine records it as an assertion."""
        source = (f"book-summarizer v1.3 headless on the team account, chapter {chapter['chapter']} "
                  f"({self.model}); driver _shared/adapters/book_summarizer_team.py")
        result = subprocess.run(
            [sys.executable, str(ENGINE), "record-author", "--task-dir", str(self.task_dir),
             "--family", "claude", "--source", source],
            capture_output=True, text=True, check=False,
        )
        self.log(f"ch{chapter['chapter']}: record-author exit {result.returncode}: "
                 f"{(result.stdout or result.stderr).strip()[:200]}")

    def record_attempt(self, chapter: dict, attempt: int, envelope: dict, exit_code: int | None,
                       classification: str, built: bool, started: float,
                       role: str = "implementer", agent: str = "book-summarizer") -> None:
        """Record the attempt's account, model, usage and cost on the contract (best effort).

        The same ``worker_attempt`` record ``dispatch-worker`` writes, marked ``source: external``,
        so ``policy_engine.py cost-report`` counts headless team production too.
        ``classification`` describes the CLI run; ``built`` says whether the chapter is done, which
        a run can achieve and still time out or exit nonzero. A failed record is logged, not
        retried.
        """
        event = {
            "account": "team", "config_dir": str(TEAM_DIR), "model": self.model,
            "role": role, "backend": f"headless:{agent}",
            "classification": classification, "built": built, "exit": exit_code, "attempt": attempt,
            "reason": f"book_summarizer_team.py ch{chapter['chapter']}",
            "duration_s": round(time.time() - started, 1),
        }
        if isinstance(envelope.get("usage"), dict):
            event["usage"] = envelope["usage"]
        if isinstance(envelope.get("total_cost_usd"), (int, float)):
            event["total_cost_usd"] = envelope["total_cost_usd"]
        result = subprocess.run(
            [sys.executable, str(ENGINE), "record-attempt", "--task-dir", str(self.task_dir),
             "--event", json.dumps(event)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            self.log(f"ch{chapter['chapter']}: record-attempt failed: "
                     f"{(result.stdout or result.stderr).strip()[:200]}")

    def render_dir(self, chapter: dict) -> pathlib.Path:
        """The chapter's page-render folder, shared by the responder and the worker (contract §5b)."""
        return self.work / f"ch{chapter['chapter']}.pages"

    def harvest(self, chapter: dict) -> list[str]:
        """Harvest the chapter's owner annotations and answer their questions; return prompt fields."""
        tag, key = f"ch{chapter['chapter']}", self.job.get("zotero_key", "none")
        if key == "none":
            return ["owner annotations: no Zotero record, nothing to harvest"]
        runs = [("ANNOTATIONS_FILE", "annotations", ["--part", "highlights",
                                                     "--pdf-pages", chapter["pdf_pages"]])]
        if str(chapter["chapter"]) == str(self.job.get("first_chapter", 1)):
            runs.append(("NOTES_FILE", "notes", ["--part", "notes"]))
        fields = []
        for name, stem, extra in runs:
            out = self.work / f"{tag}.{stem}.md"
            out.unlink(missing_ok=True)  # a harvest that dies at startup must not leave a stale file
            proc = subprocess.run(
                [sys.executable, str(self.vault / HARVEST), key, "--pdf", self.job["pdf"], *extra,
                 "--out", str(out)], cwd=self.vault, capture_output=True, text=True, check=False)
            self.log(f"{tag}: harvest {stem} exit {proc.returncode}: {proc.stdout.strip()[:160]}")
            fields.append(f"{name}: `{out}`" if out.is_file() else
                          f"{name}: not harvested (harvest exit {proc.returncode}, no file written)")
        annotations = self.work / f"{tag}.annotations.md"
        for stale in ("responses.md", "unanswered.jsonl"):  # never pass or file an earlier run's
            (self.work / f"{tag}.{stale}").unlink(missing_ok=True)
        # A crashed harvest leaves no file (the script deletes --out first); the worker then
        # records "not harvested" from the missing file instead of finding a stale one.
        if annotations.is_file() and '"action": "question"' in annotations.read_text(
                encoding="utf-8", errors="replace"):
            responses = self.work / f"{tag}.responses.md"
            if self.respond(chapter, annotations, responses):
                fields.append(f"RESPONSES_FILE: `{responses}`")
        return fields

    def respond(self, chapter: dict, annotations: pathlib.Path, responses: pathlib.Path) -> bool:
        """Run owner-question-responder headless on one harvest; True when it wrote its block."""
        tag = f"ch{chapter['chapter']}"
        prompt = "\n".join([
            f"ANNOTATIONS_FILE: `{annotations}`", f"RESPONSES_FILE: `{responses}`",
            f"UNANSWERED_FILE: `{self.work / f'{tag}.unanswered.jsonl'}`",
            f"RESPONDER_ID: {self.model}", f"PDF: `{self.job['pdf']}`",
            f"page offset: printed_page = PDF_page - {self.job['offset']}",
            f"Vault root / cwd: `{self.vault}`",
            f"RENDER_DIR: `{self.render_dir(chapter)}`",
            "Call each shell command directly, one per call.",
        ])
        argv = ["claude", "--model", self.model, "--agent", "owner-question-responder", "-p", prompt,
                "--output-format", "json", "--permission-mode", "acceptEdits",
                "--allowedTools", *RESPONDER_ALLOW, "Read", "Write", "Glob", "Grep",
                "--strict-mcp-config", "--add-dir", str(self.work)]
        attempt = 0
        while attempt < 3:
            gate.wait()
            attempt += 1
            started, timed_out = time.time(), False
            try:
                proc = subprocess.run(argv, cwd=self.vault, env=self.env, capture_output=True,
                                      text=True, timeout=self.timeout, check=False)
                out, code = proc.stdout, proc.returncode
            except subprocess.TimeoutExpired as error:
                out = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
                code, timed_out = None, True
            try:
                envelope = json.loads(out)
            except (TypeError, ValueError):
                envelope = {}
            envelope = envelope if isinstance(envelope, dict) else {}
            text = str(envelope.get("result", ""))
            wrote = responses.is_file() and "owner-question-responses:end" in responses.read_text(
                encoding="utf-8", errors="replace")
            limited = not wrote and (envelope.get("api_error_status") == 429
                                     or bool(LIMIT.search(text[:400])))
            run_class = ("timeout" if timed_out else "rate_limited" if limited
                         else "ok" if code == 0 and not envelope.get("is_error") else "error")
            self.record_attempt(chapter, attempt, envelope, code, run_class, wrote, started,
                                role="responder", agent="owner-question-responder")
            self.log(f"{tag}: responder attempt {attempt} {run_class}, wrote={wrote}, "
                     f"cost ${envelope.get('total_cost_usd', 0) or 0:.2f}")
            if wrote:
                return True
            if not limited:
                return False  # the worker then writes the explicit "unanswered" line
            attempt -= 1  # a limit wait is not a real attempt, as for the builder
            self.wait_for_reset(text)
        return False

    def file_questions(self, chapter: dict) -> None:
        """File the built chapter's unanswered owner questions for a later wiki query."""
        tag, note = f"ch{chapter['chapter']}", self.overview(chapter)
        unanswered = self.work / f"{tag}.unanswered.jsonl"
        responses = self.work / f"{tag}.responses.md"
        if not (note and responses.is_file() and "owner-question-responses:end"
                in responses.read_text(encoding="utf-8", errors="replace")):
            return  # no responder block reached this chapter's note
        if not unanswered.is_file():
            self.log(f"{tag}: questions NOT filed: the responder wrote no UNANSWERED_FILE; its "
                     f"'Cannot answer' questions are only in {note.name}")
            return
        if not unanswered.read_text(encoding="utf-8").strip():
            return
        proc = subprocess.run(
            [sys.executable, str(self.vault / HARVEST), "--file-questions", str(unanswered),
             str(self.work / f"{tag}.annotations.md"), note.stem, str(self.vault / QUEUE)],
            cwd=self.vault, capture_output=True, text=True, check=False)
        self.log(f"{tag}: file questions exit {proc.returncode}: {proc.stdout.strip()[:160]}")

    def wait_for_reset(self, text: str) -> None:
        """Pause all lanes until the reset time named in ``text`` (fallback: 30 min)."""
        with lock:
            if not gate.is_set():
                return
            gate.clear()
        now = dt.datetime.now()
        match = RESET.search(text or "")
        if match:
            hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
            moment = now.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
            if moment <= now:
                moment += dt.timedelta(days=1)
            secs = (moment - now).total_seconds() + 180
        else:
            secs = 1800
        self.log(f"TEAM ACCOUNT LIMIT -> all lanes sleep {secs / 60:.0f} min "
                 f"(until {now + dt.timedelta(seconds=secs):%m-%d %H:%M})")
        time.sleep(secs)
        gate.set()
        self.log("limit wait over, resuming")

    def build(self, chapter: dict) -> tuple[str, str]:
        """Run one chapter to completion, retrying up to 3 real attempts."""
        tag = f"ch{chapter['chapter']}"
        if self.done(chapter):
            self.log(f"{tag}: already built ({self.overview(chapter).name}), skipped")
            if not self.dry_run:  # an interrupt may have come between build and filing
                self.file_questions(chapter)
            return tag, "skipped"
        if self.dry_run:
            argv = self.command(chapter)
            print(" ".join("<prompt>" if i == 6 else a for i, a in enumerate(argv)))
            print(self.prompt(chapter))
            return tag, "dry-run"
        self.owner_fields[str(chapter["chapter"])] = self.harvest(chapter)
        attempts = 0
        while attempts < 3:
            gate.wait()
            attempts += 1
            self.log(f"{tag}: attempt {attempts} start ({self.model}, team)")
            started = time.time()
            exit_code: int | None = None
            stderr = ""
            timed_out = False
            try:
                proc = subprocess.run(self.command(chapter), cwd=self.vault, env=self.env,
                                      capture_output=True, text=True, timeout=self.timeout)
                out, exit_code, stderr = proc.stdout, proc.returncode, proc.stderr or ""
            except subprocess.TimeoutExpired as error:
                out = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
                timed_out = True
                self.log(f"{tag}: TIMEOUT after {self.timeout // 60} min")
            (self.work / f"{tag}.attempt{attempts}.json").write_text(out or "", encoding="utf-8")
            try:
                envelope = json.loads(out)
                text, err = str(envelope.get("result", "")), envelope.get("is_error")
            except (TypeError, ValueError):
                envelope, text, err = {}, out or "", True
            limited = bool(LIMIT.search(text[:400])) and not self.done(chapter)
            built = not limited and self.done(chapter)
            # The CLI run's own outcome, independent of whether the chapter got built.
            # Text and stderr count only for a run that failed: a successful summary may well
            # mention "429" (a page number) or "rate limit".
            failed = timed_out or bool(err) or exit_code not in (0, None)
            limit_signal = bool(
                (isinstance(envelope, dict) and envelope.get("api_error_status") == 429)
                or (failed and (LIMIT.search(text[:400]) or LIMIT.search(stderr[:2000])))
            )
            run_class = ("timeout" if timed_out else "rate_limited" if limit_signal
                         else "ok" if exit_code == 0 and not err else "error")
            self.record_attempt(chapter, attempts, envelope if isinstance(envelope, dict) else {},
                                exit_code, run_class, built, started)
            if limited:
                attempts -= 1  # a limit wait is not a real attempt
                self.wait_for_reset(text)
                continue
            if built:
                (self.work / f"{tag}.result.md").write_text(text, encoding="utf-8")
                self.log(f"{tag}: BUILT in {(time.time() - started) / 60:.1f} min, "
                         f"cost ${envelope.get('total_cost_usd', 0):.2f}, error_flag={err}")
                self.record_author(chapter)
                self.file_questions(chapter)
                return tag, "built"
            self.log(f"{tag}: attempt {attempts} produced no valid overview note "
                     f"(error_flag={err}); head: {text[:160]!r}")
        return tag, "FAILED"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task-dir", type=pathlib.Path, required=True)
    parser.add_argument("--job", type=pathlib.Path, required=True)
    parser.add_argument("--only", nargs="*", help="chapter numbers to run")
    parser.add_argument("--jobs", type=int, default=1,
                        help="lanes; more than 1 only when the user authorized parallel chapters")
    parser.add_argument("--timeout-min", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true", help="print the command and prompt; launch nothing")
    args = parser.parse_args()
    job = json.loads(args.job.read_text(encoding="utf-8"))
    driver = Driver(args.task_dir.resolve(), job, args.timeout_min, args.dry_run)
    chapters = job["chapters"]
    if args.only:
        chapters = [c for c in chapters if str(c["chapter"]) in args.only]
    if not chapters:
        sys.exit("no chapters selected")
    if not pathlib.Path(job["pdf"]).is_file():
        sys.exit(f"PDF not found: {job['pdf']}")
    driver.log(f"{job['citekey']}: {len(chapters)} chapter(s), {args.jobs} lane(s), model {driver.model}")
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(driver.build, chapters))
    driver.log("SUMMARY " + json.dumps(dict(results)))


if __name__ == "__main__":
    main()
