"""Tests for ``record-attempt``, ``cost-report`` and ``produce`` (the one-producer route)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

import policy_engine as pe  # noqa: E402

TEAM = {"backend": "claude-core-team", "host": "claude-code", "family": "claude",
        "model": "claude-opus-5-5", "effort": "high", "account": "team",
        "config_dir": "/tmp/team"}


def envelope(**overrides):
    """A successful Claude result envelope."""
    return {"type": "result", "subtype": "success", "is_error": False, "result": "done", **overrides}


class RecordAttemptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task_dir = Path(self.tmp.name)

    def test_needs_a_contract_and_the_required_fields(self):
        fields = {"account": "team", "model": "m", "classification": "ok"}
        self.assertFalse(pe.record_attempt(self.task_dir, fields).allowed)
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        missing = pe.record_attempt(self.task_dir, {"account": "team"})
        self.assertFalse(missing.allowed)
        self.assertIn("model", missing.reason)
        self.assertTrue(pe.record_attempt(self.task_dir, {**fields, "type": "forged"}).allowed)
        event = json.loads((self.task_dir / "events.ndjson").read_text(encoding="utf-8"))
        self.assertEqual((event["type"], event["source"]), ("worker_attempt", "external"))
        self.assertRegex(event["dispatch_id"], r"^[0-9a-f]{32}$")


class CostReportTest(unittest.TestCase):
    def test_groups_by_account_and_model_and_counts_what_is_uncosted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "b" / "runs" / "T1").mkdir(parents=True)
            events = [
                {"type": "worker_attempt", "at": "2026-09-30T01:00:00Z", "account": "team",
                 "model": "opus", "classification": "ok", "usage": {"output_tokens": 100},
                 "total_cost_usd": 1.5},
                {"type": "worker_attempt", "at": "2026-09-30T02:00:00Z", "account": "team",
                 "model": "opus", "classification": "rate_limited", "source": "external"},
                {"type": "worker_attempt", "at": "2026-09-30T03:00:00Z", "account": "private",
                 "model": "astra", "classification": "ok", "tokens_used": 4000},
                {"type": "worker_attempt", "at": "2026-09-20T00:00:00Z", "account": "team",
                 "model": "opus", "classification": "ok"},
                {"type": "something_else"},
            ]
            (root / "a" / "events.ndjson").write_text(
                "\n".join(json.dumps(e) for e in events[:2]) + "\nnot json\n", encoding="utf-8")
            (root / "b" / "runs" / "T1" / "events.ndjson").write_text(
                "\n".join(json.dumps(e) for e in events[2:]) + "\n", encoding="utf-8")
            report = pe.cost_report([root])
            rows = {(r["account"], r["model"]): r for r in report["rows"]}
            team = rows[("team", "opus")]
            self.assertEqual(team["attempts"], 3)
            self.assertEqual(team["outcomes"], {"ok": 2, "rate_limited": 1})
            self.assertEqual((team["output_tokens"], team["cost_usd"]), (100, 1.5))
            self.assertEqual((team["uncosted"], team["external"], team["tasks"]), (2, 1, 2))
            self.assertEqual(rows[("private", "astra")]["codex_tokens"], 4000)
            self.assertEqual(report["totals"]["attempts"], 4)
            self.assertEqual(report["files"], 2)
            recent = pe.cost_report([root], since="2026-09-30")
            self.assertEqual(recent["totals"]["attempts"], 3)


class ProduceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo, self.tasks = base / "repo", base / "tasks"
        (self.repo / "src").mkdir(parents=True)
        self.tasks.mkdir()
        self.brief = base / "brief.md"
        self.brief.write_text("write the thing", encoding="utf-8")

    def run_produce(self, **kwargs):
        seen = {}

        def fake_run(command, **run_kwargs):
            seen["command"] = command
            (self.repo / "src" / "x.py").write_text("print(1)\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(envelope(
                usage={"output_tokens": 7}, total_cost_usd=0.25)), stderr="")

        with mock.patch.object(pe.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(pe.shutil, "which", return_value="/usr/bin/claude"), \
                mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", TEAM), "stub")), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", **kwargs)
        return code, seen

    def test_one_call_contracts_dispatches_records_and_releases(self):
        code, seen = self.run_produce(write="src/x.py", exec_bash=True)
        self.assertEqual(code, 0)
        task_dir = self.tasks / "t1"
        contract = json.loads((task_dir / "task.yaml").read_text(encoding="utf-8"))
        self.assertEqual(contract["status"], "complete")
        self.assertEqual((contract["audit_cycles"], contract["roles_plan"]), (0, ["implementer"]))
        self.assertIsNone(contract["dispatch"]["current_role"])
        self.assertEqual(pe.validate_task(self.bundle, contract)[0], [])
        self.assertFalse((task_dir / "lease.json").exists())
        self.assertEqual((task_dir / "workers" / "implementer" / "brief.md").read_text(),
                         "write the thing")
        attempt = [e for e in pe._read_events(task_dir) if e["type"] == "worker_attempt"][0]
        self.assertEqual((attempt["account"], attempt["total_cost_usd"]), ("team", 0.25))
        record = json.loads(next((task_dir / "outputs").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual((record["status"], record["exec"]), ("succeeded", True))
        tools = seen["command"][seen["command"].index("--tools") + 1]
        self.assertTrue(tools.endswith(",Bash"), tools)

    def test_an_existing_contract_is_never_overwritten(self):
        (self.tasks / "t1").mkdir()
        (self.tasks / "t1" / "task.yaml").write_text('{"keep": true}', encoding="utf-8")
        code, _ = self.run_produce(write="src/x.py")
        self.assertEqual(code, 2)
        self.assertEqual((self.tasks / "t1" / "task.yaml").read_text(), '{"keep": true}')

    def test_exactly_one_destination_mode(self):
        with mock.patch.object(pe.sys, "stderr"):
            self.assertEqual(pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                                        owner="me"), 2)
            self.assertEqual(pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                                        owner="me", write="a", out="b"), 2)

    def test_a_dry_run_creates_nothing(self):
        with mock.patch.object(pe.shutil, "which", return_value="/usr/bin/claude"), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", write="src/x.py", exec_bash=True, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(list(self.tasks.iterdir()), [])


class BookDriverRecordTest(unittest.TestCase):
    """The headless book driver records each attempt the way dispatch-worker does."""

    def test_a_built_chapter_records_its_cost(self):
        spec = importlib.util.spec_from_file_location(
            "book_summarizer_team", ROOT / "_shared" / "adapters" / "book_summarizer_team.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            vault, task_dir = base / "vault", base / "task"
            (task_dir / "workers" / "implementer").mkdir(parents=True)
            (task_dir / "workers" / "implementer" / "brief-book.md").write_text("B", encoding="utf-8")
            (task_dir / "task.yaml").write_text(json.dumps({"target_repo": str(vault)}), encoding="utf-8")
            vault.mkdir()
            chapter = {"chapter": 20, "folder": "20_Ch", "pdf_pages": "1-2"}
            job = {"citekey": "k", "pdf": "x.pdf", "book_root": "books/B", "offset": 0,
                   "chapters": [chapter]}
            driver = module.Driver(task_dir, job, timeout_min=1, dry_run=False)
            calls = []
            real_run = subprocess.run

            def fake_run(argv, **kwargs):
                calls.append(argv)
                if argv[0] == "claude":
                    folder = driver.book_root / "20_Ch"
                    folder.mkdir(parents=True)
                    (folder / "20.00_Overview.md").write_text("agent: x\n" + "y" * 2100, encoding="utf-8")
                    return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(envelope(
                        usage={"output_tokens": 11}, total_cost_usd=0.4)), stderr="")
                return real_run(argv, **kwargs)  # the real engine: record-author, record-attempt

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run), \
                    mock.patch("builtins.print"):
                self.assertEqual(driver.build(chapter), ("ch20", "built"))
            event = [e for e in pe._read_events(task_dir) if e.get("type") == "worker_attempt"][0]
            self.assertEqual(event["source"], "external")
            self.assertEqual((event["account"], event["classification"]), ("team", "ok"))
            self.assertEqual((event["usage"], event["total_cost_usd"]), ({"output_tokens": 11}, 0.4))


if __name__ == "__main__":
    unittest.main()
