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

#: No test here may launch a real worker, even when the code under test regresses: with no
#: `claude`/`codex` on PATH, a dispatch that gets past its mocks fails at launch instead of
#: running on a real account (a 2026-09-30 mutation check did exactly that). Tests that need
#: the launch path patch `which` themselves.
_NO_LAUNCH = mock.patch.object(pe.shutil, "which", return_value=None)


def setUpModule():
    _NO_LAUNCH.start()


def tearDownModule():
    _NO_LAUNCH.stop()


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


    def test_an_external_critic_never_counts_as_an_audit_round(self):
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        fields = {"account": "private", "model": "astra", "classification": "ok", "role": "critic",
                  "dispatch_id": "f" * 32}
        self.assertTrue(pe.record_attempt(self.task_dir, fields).allowed)
        self.assertEqual(pe.completed_audit_cycles(self.task_dir), 0)
        event = json.loads((self.task_dir / "events.ndjson").read_text(encoding="utf-8"))
        self.assertNotEqual(event["dispatch_id"], "f" * 32)  # never the caller's
        with (self.task_dir / "events.ndjson").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "worker_attempt", "role": "critic",
                                     "classification": "ok", "dispatch_id": "a" * 32}) + "\n")
        self.assertEqual(pe.completed_audit_cycles(self.task_dir), 1)

    def test_malformed_fields_are_refused_and_real_usage_accepted(self):
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        base = {"account": "team", "model": "m", "classification": "ok"}
        for bad in (["not", "an", "object"], {**base, "usage": "unknown"},
                    {**base, "usage": {"output_tokens": "x"}}, {**base, "total_cost_usd": -1},
                    {**base, "total_cost_usd": float("nan")}, {**base, "tokens_used": True},
                    {**base, "model": 7}):
            with self.subTest(bad=bad):
                self.assertFalse(pe.record_attempt(self.task_dir, bad).allowed)
        real = {**base, "total_cost_usd": 0.4, "usage": {
            "input_tokens": 3, "output_tokens": 9, "service_tier": "standard",
            "server_tool_use": {"web_search_requests": 0}}}
        self.assertTrue(pe.record_attempt(self.task_dir, real).allowed)


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


    def test_malformed_history_overlapping_roots_and_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            lines = [
                {"type": "worker_attempt", "at": "2026-09-30T01:00:00Z", "account": "team",
                 "model": "m", "classification": "ok", "usage": "unknown", "total_cost_usd": "1"},
                {"type": "worker_attempt", "at": "2026-09-30T02:00:00Z", "account": "team",
                 "model": "m", "classification": "ok", "usage": {"output_tokens": "x"},
                 "tokens_used": -5},
            ]
            (root / "a" / "events.ndjson").write_text(
                "\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
            report = pe.cost_report([root])
            row = report["rows"][0]
            self.assertEqual((row["attempts"], row["uncosted"], row["output_tokens"]), (2, 2, 0))
            self.assertEqual(pe.cost_report([root, root / "a", root])["totals"], report["totals"])
            self.assertEqual(pe.cost_report([root, root / "a"])["files"], 1)
            with self.assertRaises(ValueError):
                pe.cost_report([root], since="2026-09-30T00:00")
            with mock.patch.object(pe.sys, "stdout"):
                self.assertEqual(pe.main(["cost-report", "--tasks-root", str(root),
                                          "--since", "yesterday"]), 2)


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


class ProduceFixture(unittest.TestCase):
    """Shared setup for the produce lifecycle tests; holds no tests itself."""

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
        self.brief.write_text("b", encoding="utf-8")
        self.task_dir = self.tasks / "t1"

    def produce(self, dispatch):
        with mock.patch.object(pe, "dispatch_worker", side_effect=dispatch), \
                mock.patch.object(pe.sys, "stderr"):
            return pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")

    def contract(self):
        return json.loads((self.task_dir / "task.yaml").read_text(encoding="utf-8"))

class ProduceLifecycleTest(ProduceFixture):
    """Setup is exclusive; finalization keeps others' edits and never writes a lost task."""

    def test_an_existing_folder_or_link_is_refused_before_anything_is_written(self):
        other = self.tasks / "other"
        other.mkdir()
        (other / "task.yaml").write_text('{"keep": true}', encoding="utf-8")
        for make in (lambda: self.task_dir.mkdir(), lambda: self.task_dir.symlink_to(other),
                     lambda: self.task_dir.symlink_to(self.tasks / "missing")):
            with self.subTest(make=make):
                make()
                self.assertEqual(self.produce(lambda *a, **k: 0), 2)
                self.assertEqual((other / "task.yaml").read_text(), '{"keep": true}')
                self.assertEqual(sorted(p.name for p in other.iterdir()), ["task.yaml"])
                if self.task_dir.is_symlink():
                    self.task_dir.unlink()
                else:
                    self.task_dir.rmdir()

    def test_a_failed_dispatch_leaves_a_valid_failed_contract_and_no_lease(self):
        self.assertEqual(self.produce(lambda *a, **k: 2), 2)
        contract = self.contract()
        self.assertEqual(contract["status"], "failed")
        self.assertIsNone(contract["dispatch"]["current_role"])
        self.assertEqual(pe.validate_task(self.bundle, contract)[0], [])
        self.assertFalse((self.task_dir / "lease.json").exists())

    def test_a_raising_dispatch_still_finalizes_and_releases(self):
        def boom(*args, **kwargs):
            raise RuntimeError("worker launcher exploded")

        with self.assertRaises(RuntimeError):
            self.produce(boom)
        self.assertEqual(self.contract()["status"], "failed")
        self.assertFalse((self.task_dir / "lease.json").exists())

    def test_an_edit_made_during_the_run_survives_finalization(self):
        def edit(*args, **kwargs):
            contract = self.contract()
            contract["notes"] = "edited mid-run"
            (self.task_dir / "task.yaml").write_text(json.dumps(contract), encoding="utf-8")
            return 0

        self.assertEqual(self.produce(edit), 0)
        self.assertEqual((self.contract()["notes"], self.contract()["status"]),
                         ("edited mid-run", "complete"))

    def test_a_task_whose_lease_moved_is_not_written(self):
        def steal(*args, **kwargs):
            lease = json.loads((self.task_dir / "lease.json").read_text(encoding="utf-8"))
            lease.update(owner="someone-else", acquired_at="2099-01-01T00:00:00Z")
            (self.task_dir / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
            return 0

        self.assertEqual(self.produce(steal), 0)
        self.assertEqual(self.contract()["status"], "active")
        self.assertEqual(json.loads((self.task_dir / "lease.json").read_text())["owner"], "someone-else")

    def test_an_invalid_policy_is_refused(self):
        with mock.patch.object(pe, "validate_policy", return_value=(["broken"], [])):
            self.assertEqual(self.produce(lambda *a, **k: 0), 2)
        self.assertFalse(self.task_dir.exists())


class ProduceRound2Test(ProduceFixture):
    """Audit round 2: staging, same-owner successors, unreadable leases, failed acquisition."""

    def produce_capturing(self, dispatch):
        import io
        err = io.StringIO()
        with mock.patch.object(pe, "dispatch_worker", side_effect=dispatch), \
                mock.patch.object(pe.sys, "stderr", err):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        line = [l for l in err.getvalue().splitlines() if l.startswith("produce: ")][-1]
        return code, json.loads(line[len("produce: "):])

    def test_finalization_uses_no_predictable_staging_name(self):
        def plant(*args, **kwargs):
            (self.task_dir / "task.yaml.partial").write_text("a producer's output", encoding="utf-8")
            return 0

        code, summary = self.produce_capturing(plant)
        self.assertEqual((code, summary["status"]), (0, "complete"))
        self.assertEqual((self.task_dir / "task.yaml.partial").read_text(), "a producer's output")
        self.assertEqual(list(self.task_dir.glob(".task.*.tmp")), [])

    def test_a_linked_contract_is_not_written_through(self):
        outside = self.tasks / "elsewhere.yaml"

        def relink(*args, **kwargs):
            outside.write_text((self.task_dir / "task.yaml").read_text(), encoding="utf-8")
            (self.task_dir / "task.yaml").unlink()
            (self.task_dir / "task.yaml").symlink_to(outside)
            return 0

        code, summary = self.produce_capturing(relink)
        self.assertTrue(summary["status"].startswith("skipped"), summary)
        self.assertEqual(json.loads(outside.read_text())["status"], "active")

    def test_a_same_owner_successor_lease_is_not_released(self):
        def successor(*args, **kwargs):
            lease = json.loads((self.task_dir / "lease.json").read_text(encoding="utf-8"))
            lease["acquired_at"] = "2099-01-01T00:00:00Z"
            (self.task_dir / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
            return 0

        code, summary = self.produce_capturing(successor)
        self.assertFalse(summary["lease_released"])
        self.assertTrue((self.task_dir / "lease.json").exists())
        self.assertEqual(self.contract()["status"], "active")

    def test_an_unreadable_lease_still_ends_with_a_summary(self):
        def corrupt(*args, **kwargs):
            (self.task_dir / "lease.json").write_text("{ not json", encoding="utf-8")
            return 0

        code, summary = self.produce_capturing(corrupt)
        self.assertEqual(code, 0)
        self.assertFalse(summary["lease_released"])
        self.assertTrue(summary["status"].startswith("skipped"))

    def test_a_refused_or_raising_acquisition_leaves_the_contract_pending(self):
        # Round 4: without the lease this run never owned the task, so it writes nothing to the
        # contract; `pending` says it never started.
        with mock.patch.object(pe, "acquire_lease", return_value=pe.Decision(False, "busy")):
            code, summary = self.produce_capturing(lambda *a, **k: 0)
        self.assertEqual(code, 2)
        self.assertTrue(summary["status"].startswith("pending"), summary)
        self.assertEqual(self.contract()["status"], "pending")
        self.assertEqual(pe.validate_task(self.bundle, self.contract())[0], [])
        self.task_dir = self.tasks / "t2"
        with mock.patch.object(pe, "acquire_lease", side_effect=OSError("disk gone")), \
                mock.patch.object(pe.sys, "stderr"), self.assertRaises(OSError):
            pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks, owner="me",
                       task_id="t2", write="src/x.py")
        self.assertEqual(self.contract()["status"], "pending")

    def test_the_contract_is_active_only_while_this_run_holds_the_lease(self):
        seen = {}

        def look(*args, **kwargs):
            seen["during"] = self.contract()["status"]
            return 0

        code, summary = self.produce_capturing(look)
        self.assertEqual((seen["during"], summary["status"], self.contract()["status"]),
                         ("active", "complete", "complete"))


class ProduceRound3Test(ProduceFixture):
    """Audit round 3: a competing owner's contract, and cleanup that must never raise."""

    def run_capturing(self, dispatch, **patches):
        import io
        err = io.StringIO()
        with mock.patch.object(pe, "dispatch_worker", side_effect=dispatch), \
                mock.patch.object(pe.sys, "stderr", err):
            try:
                code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                                  owner="me", task_id="t1", write="src/x.py")
            except RuntimeError as error:
                code = error
        line = [l for l in err.getvalue().splitlines() if l.startswith("produce: ")][-1]
        return code, json.loads(line[len("produce: "):])

    def test_a_competing_owner_that_acquired_first_keeps_its_contract(self):
        real_acquire = pe.acquire_lease

        def competitor_first(task_dir, owner, ttl):
            real_acquire(task_dir, "other", 600)
            return real_acquire(task_dir, owner, ttl)  # refused: leased by other

        with mock.patch.object(pe, "acquire_lease", side_effect=competitor_first):
            code, summary = self.run_capturing(lambda *a, **k: 0)
        self.assertEqual(code, 2)
        self.assertTrue(summary["status"].startswith("pending"), summary)
        self.assertEqual(self.contract()["status"], "pending")
        self.assertEqual(json.loads((self.task_dir / "lease.json").read_text())["owner"], "other")

    def test_a_competitor_that_finished_and_released_keeps_its_result(self):
        # Round 4's interleaving: the competitor acquires, completes, marks the contract and
        # releases, all before the refused caller cleans up. No lease is left, and still the
        # refused caller must not touch the contract.
        real_acquire = pe.acquire_lease

        def competitor_done(task_dir, owner, ttl):
            generation = real_acquire(task_dir, "other", 600).details["acquired_at"]
            refused = real_acquire(task_dir, owner, ttl)
            pe._finalize_produced(task_dir, task_dir / "task.yaml", "other", generation, "complete")
            pe.release_lease(task_dir, "other", generation)
            return refused

        with mock.patch.object(pe, "acquire_lease", side_effect=competitor_done):
            code, summary = self.run_capturing(lambda *a, **k: 0)
        self.assertEqual(code, 2)
        self.assertFalse((self.task_dir / "lease.json").exists())
        self.assertEqual(self.contract()["status"], "complete")

    def test_a_failed_contract_publish_takes_no_lease_and_deletes_nothing(self):
        # produce never deletes: the half-built folder stays as a record, with no contract, and
        # no lease was possible without one.
        import io
        acquired, err = [], io.StringIO()
        with mock.patch.object(pe.os, "link", side_effect=OSError(28, "No space left on device")), \
                mock.patch.object(pe, "acquire_lease", side_effect=lambda *a: acquired.append(a)), \
                mock.patch.object(pe, "dispatch_worker") as dispatched, \
                mock.patch.object(pe.sys, "stderr", err):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        self.assertEqual((code, acquired), (2, []))
        dispatched.assert_not_called()
        self.assertIn("left as it is", err.getvalue())
        summaries = [l for l in err.getvalue().splitlines() if l.startswith("produce: {")]
        self.assertEqual(summaries, [])  # nothing was published, so no lifecycle summary
        self.assertFalse((self.task_dir / "task.yaml").exists())
        self.assertTrue((self.task_dir / "workers" / "implementer" / "brief.md").exists())

    def test_a_published_contract_is_complete_and_no_rewrite_temp_file_remains(self):
        code, summary = self.run_capturing(lambda *a, **k: 0)
        self.assertEqual(code, 0)
        self.assertEqual(pe.validate_task(self.bundle, self.contract())[0], [])
        self.assertEqual(list(self.task_dir.glob(".task.*.tmp")), [])

    def test_the_initial_contract_record_stays_and_nothing_is_deleted_by_name(self):
        # Round 10: removing a staging file by name could remove someone else's file, so the
        # hard-linked initial contract stays, and a failed rewrite leaves its own temp file too.
        code, summary = self.run_capturing(lambda *a, **k: 0)
        self.assertEqual((code, summary["status"]), (0, "complete"))
        initial = list(self.task_dir.glob(".task-initial.*.json"))
        self.assertEqual(len(initial), 1)
        self.assertEqual(json.loads(initial[0].read_text())["status"], "pending")
        with mock.patch.object(pe.os, "replace", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                pe._rewrite_contract_status(self.task_dir / "task.yaml", "failed")
        self.assertEqual(len(list(self.task_dir.glob(".task.*.tmp"))), 1)

    def test_a_brief_planted_before_its_write_is_never_overwritten(self):
        real_mkdir = Path.mkdir

        def mkdir_then_plant(path, *args, **kwargs):
            real_mkdir(path, *args, **kwargs)
            if path.name == "implementer":
                (path / "brief.md").write_text("theirs", encoding="utf-8")

        with mock.patch.object(Path, "mkdir", mkdir_then_plant), \
                mock.patch.object(pe, "dispatch_worker") as dispatched, \
                mock.patch.object(pe.sys, "stderr"):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        dispatched.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual((self.task_dir / "workers" / "implementer" / "brief.md").read_text(), "theirs")
        self.assertFalse((self.task_dir / "task.yaml").exists())

    def test_an_unreadable_brief_reserves_nothing_and_the_id_stays_free(self):
        with mock.patch.object(pe.sys, "stderr"), \
                mock.patch.object(pe, "dispatch_worker", return_value=0):
            code = pe.produce(self.bundle, self.repo, self.tasks.parent / "missing.md",
                              tasks_root=self.tasks, owner="me", task_id="t1", write="src/x.py")
        self.assertEqual(code, 2)
        self.assertFalse(self.task_dir.exists())
        code, summary = self.run_capturing(lambda *a, **k: 0)
        self.assertEqual((code, summary["status"]), (0, "complete"))

    def test_a_malformed_lease_counter_does_not_escape_cleanup(self):
        def null_counter(*args, **kwargs):
            lease = json.loads((self.task_dir / "lease.json").read_text(encoding="utf-8"))
            lease["active_workers"] = None
            (self.task_dir / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
            return 0

        code, summary = self.run_capturing(null_counter)
        self.assertEqual(code, 0)
        self.assertFalse(summary["lease_released"])

    def test_unreadable_events_keep_the_dispatch_exception_and_the_summary(self):
        def boom(*args, **kwargs):
            (self.task_dir / "events.ndjson").mkdir()  # reading it now raises
            raise RuntimeError("the worker launcher exploded")

        error, summary = self.run_capturing(boom)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("events unreadable", summary["attempts"])


class ProduceInterruptTest(ProduceFixture):
    """Round 7: an interrupt after publication still ends in a summary; one before it frees the ID."""

    def interrupted(self, **patches):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(pe, "dispatch_worker", return_value=0))
            stack.enter_context(mock.patch.object(pe.sys, "stderr", err))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(pe, name, value))
            with self.assertRaises(KeyboardInterrupt):
                pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                           owner="me", task_id="t1", write="src/x.py")
        lines = [l for l in err.getvalue().splitlines() if l.startswith("produce: ")]
        return json.loads(lines[-1][len("produce: "):]) if lines else None

    def test_an_interrupt_just_after_a_successful_link_ends_in_a_summary(self):
        real_link = pe.os.link

        def link_then_interrupt(src, dst, *args, **kwargs):
            real_link(src, dst, *args, **kwargs)
            raise KeyboardInterrupt

        with mock.patch.object(pe.os, "link", side_effect=link_then_interrupt):
            summary = self.interrupted()
        self.assertIsNotNone(summary)
        self.assertTrue(summary["status"].startswith("pending"), summary)
        self.assertEqual(self.contract()["status"], "pending")

    def test_an_interrupted_acquisition_ends_in_a_summary(self):
        summary = self.interrupted(acquire_lease=mock.Mock(side_effect=KeyboardInterrupt))
        self.assertTrue(summary["status"].startswith("pending"), summary)

    def test_an_interrupt_at_the_link_keeps_the_folder(self):
        # It may surface just after a link that succeeded, so the task may be visible: keep it.
        with mock.patch.object(pe.os, "link", side_effect=KeyboardInterrupt):
            summary = self.interrupted()
        self.assertTrue(summary["status"].startswith("pending"), summary)

    def test_an_interrupt_before_the_link_leaves_the_folder_and_no_summary(self):
        with mock.patch.object(pe.tempfile, "mkstemp", side_effect=KeyboardInterrupt):
            summary = self.interrupted()
        self.assertIsNone(summary)
        self.assertTrue(self.task_dir.exists())
        self.assertFalse((self.task_dir / "task.yaml").exists())

    def test_a_failed_setup_deletes_nothing_it_finds(self):
        # Round 9: a rollback cannot prove which files are its own, so there is none. Anything
        # another process put in the folder -- even at this call's own paths -- stays.
        def foreign_then_fail(*args, **kwargs):
            (self.task_dir / "observed-author.json").write_text("{}", encoding="utf-8")
            (self.task_dir / "workers" / "implementer" / "brief.md").write_text("theirs", encoding="utf-8")
            raise OSError(28, "No space left on device")

        with mock.patch.object(pe.tempfile, "mkstemp", side_effect=foreign_then_fail), \
                mock.patch.object(pe, "dispatch_worker") as dispatched, \
                mock.patch.object(pe.sys, "stderr"):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        self.assertEqual(code, 2)
        dispatched.assert_not_called()
        self.assertTrue((self.task_dir / "observed-author.json").exists())
        self.assertEqual((self.task_dir / "workers" / "implementer" / "brief.md").read_text(), "theirs")

    def test_a_contract_removed_during_the_run_keeps_the_records(self):
        # Round 8's P0: an absent task.yaml after publication must not look unpublished.
        def remove_contract(*args, **kwargs):
            (self.task_dir / "events.ndjson").write_text('{"type": "worker_attempt"}\n', encoding="utf-8")
            (self.task_dir / "outputs").mkdir()
            (self.task_dir / "outputs" / "record.json").write_text("{}", encoding="utf-8")
            (self.task_dir / "task.yaml").unlink()
            return 0

        import io
        err = io.StringIO()
        with mock.patch.object(pe, "dispatch_worker", side_effect=remove_contract), \
                mock.patch.object(pe.sys, "stderr", err):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        self.assertEqual(code, 0)
        self.assertTrue((self.task_dir / "events.ndjson").exists())
        self.assertTrue((self.task_dir / "outputs" / "record.json").exists())
        summary = json.loads([l for l in err.getvalue().splitlines() if l.startswith("produce: ")][-1][9:])
        self.assertTrue(summary["lease_released"], summary)

    def test_a_contract_planted_before_publication_is_left_alone(self):
        real_link = pe.os.link

        def plant_then_link(src, dst, *args, **kwargs):
            Path(dst).write_text('{"planted": true}', encoding="utf-8")
            return real_link(src, dst, *args, **kwargs)  # FileExistsError: exclusive

        with mock.patch.object(pe.os, "link", side_effect=plant_then_link), \
                mock.patch.object(pe, "dispatch_worker") as dispatched, \
                mock.patch.object(pe.sys, "stderr"):
            code = pe.produce(self.bundle, self.repo, self.brief, tasks_root=self.tasks,
                              owner="me", task_id="t1", write="src/x.py")
        self.assertEqual(code, 2)
        dispatched.assert_not_called()
        self.assertEqual((self.task_dir / "task.yaml").read_text(), '{"planted": true}')
        self.assertTrue((self.task_dir / "workers" / "implementer" / "brief.md").exists())


class CodexLimitClassificationTest(unittest.TestCase):
    """A Codex usage-limit exit is `rate_limited`, not `error` (OPEN_ITEMS A10, captured 2026-09-23)."""

    CAPTURED = ("ERROR: You\u2019ve hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at Sep 28th, 2026 6:24 PM.\n")

    def classify(self, returncode, stderr):
        spec = pe._codex_cli({"model": "m", "effort": "low"})
        done = subprocess.CompletedProcess([], returncode, stdout=None, stderr=stderr)
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("b", encoding="utf-8")
            with mock.patch.object(pe.subprocess, "run", return_value=done):
                return pe._run_worker(["codex"], spec, {"task_id": "t"}, "critic", brief,
                                      Path(tmp), {"model": "m"}).classification

    def test_the_captured_usage_limit_is_rate_limited(self):
        self.assertEqual(self.classify(1, "progress...\n" + self.CAPTURED), "rate_limited")

    def test_reviewed_text_that_mentions_rate_limits_is_not_a_limit(self):
        log = ("user\nERROR: You\u2019ve hit your usage limit (quoted in the brief)\n" + "progress\n" * 5
               + 'Codex saw "ERROR: rate limit" in the log.\nERROR: stream disconnected\n')
        self.assertEqual(self.classify(1, log), "error")

    def test_a_clean_exit_is_ok_whatever_stderr_says(self):
        self.assertEqual(self.classify(0, self.CAPTURED), "ok")


class ReviewCopyTest(unittest.TestCase):
    """--review-copy: a Codex reviewer runs with a writable sandbox in a disposable copy (step 5)."""

    CODEX = {"backend": "codex-ceiling", "host": "codex", "family": "codex", "model": "gpt-x",
             "effort": "medium", "account": "private"}

    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "x.py").write_text("print(1)\n", encoding="utf-8")
        self.task_dir = base / "task"
        self.task_dir.mkdir()
        self.brief = self.task_dir / "brief.md"
        self.brief.write_text("review it", encoding="utf-8")
        self.contract_path = self.task_dir / "task.yaml"
        self.contract = {
            "schema_version": 1, "task_id": "t", "status": "active", "target_repo": str(self.repo),
            "write_scope": [], "roles_plan": ["critic"], "audit_cycles": 1, "author_family": "claude",
            "approvals": {"user": []}, "dispatch": {"current_role": "critic"},
            "conductor": {"host": "claude-code", "backend": "claude-frontier", "lease_owner": "me"},
        }
        self.contract_path.write_text(json.dumps(self.contract), encoding="utf-8")
        self.assertTrue(pe.acquire_lease(self.task_dir, "me", 600).allowed)

    def dispatch(self, backend=None, role="critic", dry_run=False, run=None):
        seen = {}

        def fake_run(command, **kwargs):
            seen.update(command=command, **kwargs)
            if run:
                run(Path(kwargs["cwd"]))
            return subprocess.CompletedProcess(command, 0, stdout=None, stderr="tokens used\n5\n")

        with mock.patch.object(pe.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(pe.shutil, "which", return_value="/usr/bin/codex"), \
                mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", backend or self.CODEX), "stub")), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.dispatch_worker(
                self.bundle, dict(self.contract), role, self.brief, "native", dry_run, None,
                self.task_dir, self.task_dir, None, self.contract_path, 0, None, False, True)
        return code, seen

    def test_the_reviewer_writes_in_a_copy_that_is_removed_and_the_original_is_untouched(self):
        def run(cwd):
            self.assertNotEqual(cwd.resolve(), self.repo.resolve())
            self.assertTrue(cwd.parent.name.startswith("multiagent-review-"))
            self.assertTrue((cwd / "src" / "x.py").exists())
            self.assertFalse((cwd / ".git").exists())
            (cwd / "scratch.txt").write_text("test output", encoding="utf-8")

        code, seen = self.dispatch(run=run)
        self.assertEqual(code, 0)
        self.assertIn("workspace-write", seen["command"])
        self.assertIn("disposable copy of the repository", seen["input"])
        self.assertFalse(Path(seen["cwd"]).parent.exists())
        self.assertFalse((self.repo / "scratch.txt").exists())
        event = [e for e in pe._read_events(self.task_dir) if e.get("type") == "worker_attempt"][0]
        self.assertTrue(event["review_copy"])

    def test_only_a_codex_critic_or_verifier_may_use_it(self):
        code, seen = self.dispatch(role="implementer")
        self.assertEqual((code, seen), (2, {}))
        claude = {"backend": "claude-ceiling", "host": "claude-code", "family": "claude",
                  "model": "m", "effort": "high", "account": "private"}
        code, seen = self.dispatch(backend=claude)
        self.assertEqual((code, seen), (2, {}))
        with self.assertRaises(NotImplementedError):
            pe.build_worker_command(claude, "native", self.repo, "critic", review_copy=True)

    def test_a_target_over_the_limit_is_refused_before_copying(self):
        with mock.patch.object(pe, "REVIEW_COPY_MAX_FILES", 0), \
                mock.patch.object(pe.tempfile, "mkdtemp") as made:
            code, seen = self.dispatch()
        self.assertEqual((code, seen), (2, {}))
        made.assert_not_called()

    def test_a_dry_run_makes_no_copy_and_the_default_stays_read_only(self):
        with mock.patch.object(pe.tempfile, "mkdtemp") as made:
            code, seen = self.dispatch(dry_run=True)
        self.assertEqual(code, 0)
        made.assert_not_called()
        with mock.patch.object(pe.shutil, "which", return_value="/usr/bin/codex"):
            plain = pe.build_worker_command(self.CODEX, "native", self.repo, "critic")[1]
            copied = pe.build_worker_command(self.CODEX, "native", self.repo, "critic", review_copy=True)[1]
        self.assertIn("read-only", plain.args)
        self.assertNotIn("workspace-write", plain.args)
        self.assertEqual(copied.enforcement, pe.REVIEW_COPY_ENFORCEMENT)


class Round3AccountingTest(unittest.TestCase):
    def test_an_integer_past_the_digit_limit_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "task.yaml").write_text("{}", encoding="utf-8")
            (task_dir / "events.ndjson").write_text(
                '{"type": "worker_attempt", "account": "team", "model": "m", "classification": "ok", '
                '"total_cost_usd": ' + "9" * 5000 + '}\n'
                '{"type": "worker_attempt", "account": "team", "model": "m", "classification": "ok"}\n',
                encoding="utf-8")
            self.assertEqual(pe.cost_report([task_dir])["totals"]["attempts"], 1)
            self.assertEqual(len(pe._read_events(task_dir)), 1)
            with mock.patch.object(pe.sys, "stdout"):
                self.assertEqual(pe.main(["record-attempt", "--task-dir", str(task_dir), "--event",
                                          '{"total_cost_usd": ' + "9" * 5000 + '}']), 2)


class Round2AccountingTest(unittest.TestCase):
    def test_huge_numbers_are_refused_and_tolerated_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "task.yaml").write_text("{}", encoding="utf-8")
            base = {"account": "team", "model": "m", "classification": "ok"}
            self.assertFalse(pe.record_attempt(task_dir, {**base, "total_cost_usd": 10 ** 400}).allowed)
            self.assertFalse(pe.record_attempt(task_dir, {**base, "usage": {"output_tokens": 10 ** 400}}).allowed)
            (task_dir / "events.ndjson").write_text(
                '{"type": "worker_attempt", "account": "team", "model": "m", "classification": "ok", '
                '"total_cost_usd": ' + "9" * 401 + ', "usage": {"output_tokens": ' + "9" * 401 + '}}\n',
                encoding="utf-8")
            row = pe.cost_report([task_dir])["rows"][0]
            self.assertEqual((row["uncosted"], row["output_tokens"], row["cost_usd"]), (1, 0, 0.0))
            with self.assertRaises(ValueError):
                pe.cost_report([task_dir], since="2026-99-99")
            self.assertEqual(pe.cost_report([task_dir], since="2026-02-28")["files"], 1)


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
            self.assertEqual((event["account"], event["classification"], event["built"]),
                             ("team", "ok", True))
            self.assertEqual((event["usage"], event["total_cost_usd"]), ({"output_tokens": 11}, 0.4))




class BookDriverOutcomeTest(unittest.TestCase):
    """The recorded classification is the CLI run's; whether the chapter got built is separate."""

    def run_one(self, finish):
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
            real_run = subprocess.run

            def fake_run(argv, **kwargs):
                if argv[0] != "claude":
                    return real_run(argv, **kwargs)
                folder = driver.book_root / "20_Ch"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "20.00_Overview.md").write_text("agent: x\n" + "y" * 2100, encoding="utf-8")
                return finish(argv)

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run), \
                    mock.patch("builtins.print"):
                self.assertEqual(driver.build(chapter), ("ch20", "built"))
            return [e for e in pe._read_events(task_dir) if e.get("type") == "worker_attempt"][0]

    def test_a_timeout_after_the_chapter_was_built(self):
        def timeout(argv):
            raise subprocess.TimeoutExpired(argv, 60, output=json.dumps(envelope()))

        event = self.run_one(timeout)
        self.assertEqual((event["classification"], event["built"], event["exit"]), ("timeout", True, None))

    def test_a_rate_limit_after_the_chapter_was_built(self):
        by_status = self.run_one(lambda argv: subprocess.CompletedProcess(
            argv, 1, stdout=json.dumps(envelope(is_error=True, api_error_status=429, result="x")),
            stderr=""))
        self.assertEqual((by_status["classification"], by_status["built"]), ("rate_limited", True))
        by_stderr = self.run_one(lambda argv: subprocess.CompletedProcess(
            argv, 1, stdout=json.dumps(envelope(is_error=True, result="failed")),
            stderr="API Error: 429 rate limit"))
        self.assertEqual((by_stderr["classification"], by_stderr["built"]), ("rate_limited", True))

    def test_successful_content_that_mentions_429_is_not_a_rate_limit(self):
        event = self.run_one(lambda argv: subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(envelope(result="Summarized pages 429-450 on rate limits.")),
            stderr=""))
        self.assertEqual((event["classification"], event["built"]), ("ok", True))

    def test_a_nonzero_exit_after_the_chapter_was_built(self):
        event = self.run_one(lambda argv: subprocess.CompletedProcess(
            argv, 1, stdout=json.dumps(envelope(is_error=True)), stderr=""))
        self.assertEqual((event["classification"], event["built"], event["exit"]), ("error", True, 1))


if __name__ == "__main__":
    unittest.main()
