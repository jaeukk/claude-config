"""Checks for the headless team driver's decisions: done detection, launch argv, reset parsing."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "book_summarizer_team", ROOT / "_shared" / "adapters" / "book_summarizer_team.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DriverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.vault = self.tmp / "vault"
        (self.vault / ".claude" / "agents").mkdir(parents=True)
        task_dir = self.tmp / "task"
        (task_dir / "workers" / "implementer").mkdir(parents=True)
        (task_dir / "workers" / "implementer" / "brief-book.md").write_text("BRIEF\n", encoding="utf-8")
        (task_dir / "task.yaml").write_text(json.dumps({"target_repo": str(self.vault)}), encoding="utf-8")
        self.task_dir = task_dir
        self.chapter = {"chapter": 20, "folder": "20_Ch", "pdf_pages": "1-2"}

    def driver(self, book_root: str) -> "MODULE.Driver":
        job = {"citekey": "k", "pdf": "x.pdf", "book_root": book_root, "offset": 0, "chapters": [self.chapter]}
        return MODULE.Driver(self.task_dir, job, timeout_min=1, dry_run=True)

    def test_done_requires_attributed_overview(self) -> None:
        driver = self.driver("books/B")
        folder = driver.book_root / "20_Ch"
        folder.mkdir(parents=True)
        self.assertFalse(driver.done(self.chapter))
        note = folder / "20.00_Overview.md"
        note.write_text("---\nagent: x\n---\n" + "y" * 2500, encoding="utf-8")
        self.assertTrue(driver.done(self.chapter))
        note.write_text("---\ntitle: no agent line\n---\n" + "y" * 2500, encoding="utf-8")
        self.assertFalse(driver.done(self.chapter))

    def test_command_grants_only_an_external_book_root(self) -> None:
        inside = self.driver("books/B").command(self.chapter)
        self.assertNotIn("--add-dir", inside)
        outside = self.driver(str(self.tmp / "elsewhere")).command(self.chapter)
        self.assertEqual(outside[outside.index("--add-dir") + 1], str(self.tmp / "elsewhere"))
        self.assertNotIn("Bash", outside)  # the shell grant is a named allowlist, never bare
        self.assertIn("Bash(pdftoppm:*)", outside)
        self.assertIn("BUILDER_ID: claude-sonnet-5-5", outside[outside.index("-p") + 1])

    def test_unanswered_questions_are_filed_after_the_build(self) -> None:
        driver = self.driver("books/B")
        folder = driver.book_root / "20_Ch"
        folder.mkdir(parents=True)
        (folder / "20.00_Overview.md").write_text("agent: x\n" + "x" * 2100, encoding="utf-8")
        unanswered = driver.work / "ch20.unanswered.jsonl"
        responses = driver.work / "ch20.responses.md"
        calls, logs, real = [], [], MODULE.subprocess.run
        MODULE.subprocess.run = lambda argv, **kw: calls.append(argv) or types.SimpleNamespace(
            returncode=0, stdout="filed     K1"
        )
        driver.log = logs.append
        try:
            unanswered.write_text('{"key": "K1", "answer": "Cannot answer."}\n', encoding="utf-8")
            driver.file_questions(self.chapter)
            self.assertEqual(calls, [])  # no responder block reached the note: nothing to file
            responses.write_text("<!-- owner-question-responses:end -->\n", encoding="utf-8")
            driver.file_questions(self.chapter)
            self.assertEqual(
                calls[-1][2:],
                ["--file-questions", str(unanswered), str(driver.work / "ch20.annotations.md"),
                 "20.00_Overview", str(driver.vault / MODULE.QUEUE)],
            )
            unanswered.unlink()
            driver.file_questions(self.chapter)
            self.assertIn("NOT filed", logs[-1])  # a missing list is reported, never skipped silently
            self.assertEqual(len(calls), 1)
            unanswered.write_text('{"key": "K1", "answer": "Cannot answer."}\n', encoding="utf-8")
            driver.dry_run = False
            self.assertEqual(driver.build(self.chapter), ("ch20", "skipped"))
            self.assertEqual(len(calls), 2)  # a restart files what an interrupt left pending
            driver.dry_run = True
            driver.build(self.chapter)
            self.assertEqual(len(calls), 2)  # a dry run never files
        finally:
            MODULE.subprocess.run = real

    def test_reset_regex_reads_the_cli_wording(self) -> None:
        match = MODULE.RESET.search("You've hit your limit · resets 11:30pm (Asia/Seoul)")
        self.assertEqual((match.group(1), match.group(2), match.group(3)), ("11", "30", "pm"))
        self.assertIsNone(MODULE.RESET.search("no limit text here"))


if __name__ == "__main__":
    unittest.main()
