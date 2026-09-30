"""Tests for write-scope matching and lease renewal.

Both fixes here came from a live run: a ``write_scope`` written as ``"docs/**"``
matched nothing, and a dispatch's own heartbeat cut a two-hour task lease down to
five minutes.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
import unittest.mock
from unittest import mock
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

import policy_engine as pe  # noqa: E402


class ScopeGlobTest(unittest.TestCase):
    """A trailing ``/**`` is the conventional spelling of a directory prefix."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_trailing_globs_match_the_directory(self):
        for scope in ("docs", "docs/", "docs/*", "docs/**"):
            with self.subTest(scope):
                self.assertTrue(pe._is_in_scope("docs/note.md", str(self.repo), [scope]))

    def test_a_bare_glob_is_the_whole_repo(self):
        self.assertTrue(pe._is_in_scope("docs/note.md", str(self.repo), ["**"]))

    def test_an_unrelated_directory_still_does_not_match(self):
        self.assertFalse(pe._is_in_scope("src/note.md", str(self.repo), ["docs/**"]))

    def test_a_non_trailing_glob_is_rejected_by_the_validator(self):
        # It would resolve as a literal directory name and silently match nothing,
        # which is exactly how the live run lost every publication.
        bundle = pe.load_policy(ROOT)
        task = {
            "schema_version": 1, "task_id": "t", "status": "active", "target_repo": str(ROOT),
            "write_scope": ["docs/*/notes"], "roles_plan": [], "author_family": "claude",
            "conductor": {"host": "claude-code", "backend": "claude-frontier",
                          "lease_owner": "me"},
            "dispatch": {"current_role": None, "active_workers": 0}, "approvals": {"user": []},
        }
        errors, _ = pe.validate_task(bundle, task)
        self.assertTrue(any("trailing" in error for error in errors), errors)
        task["write_scope"] = ["docs/**"]
        errors, _ = pe.validate_task(bundle, task)
        self.assertEqual(errors, [], errors)


class HeartbeatTtlTest(unittest.TestCase):
    """A heartbeat extends a lease; it never shortens one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.tmp.name)
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_a_short_heartbeat_does_not_cut_a_long_lease(self):
        acquired = pe.acquire_lease(self.task_dir, "me", ttl_seconds=7200)
        self.assertTrue(acquired.allowed, acquired.reason)
        expiry = acquired.details["expires_epoch"]
        beat = pe.heartbeat_lease(self.task_dir, "me", pe.WORKER_LEASE_TTL)
        self.assertTrue(beat.allowed, beat.reason)
        self.assertEqual(beat.details["expires_epoch"], expiry)
        self.assertGreater(beat.details["expires_epoch"], time.time() + 3600)

    def test_a_longer_heartbeat_still_extends(self):
        pe.acquire_lease(self.task_dir, "me", ttl_seconds=60)
        beat = pe.heartbeat_lease(self.task_dir, "me", 7200)
        self.assertGreater(beat.details["expires_epoch"], time.time() + 3600)


class LeaseNeedsContractTest(unittest.TestCase):
    """A lease or event lands only in a folder that holds its contract (2026-09-27 stray)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_no_lease_and_no_folder_without_a_contract(self):
        stray = self.root / "2026-09-27-some-task"
        decision = pe.acquire_lease(stray, "me")
        self.assertFalse(decision.allowed)
        self.assertIn("task.yaml", decision.reason)
        self.assertFalse(stray.exists())
        self.root.joinpath("lease.json").write_text("{}", encoding="utf-8")
        self.assertFalse(pe.append_event(self.root, "me", {"type": "x"}).allowed)
        self.assertFalse(self.root.joinpath("events.ndjson").exists())

    def test_abbreviated_options_are_refused(self):
        # `--task` was once accepted as `--task-dir`, which is how the stray folder was made.
        with self.assertRaises(SystemExit), \
                unittest.mock.patch("sys.stderr"):
            pe.main(["acquire-lease", "--task", str(self.root), "--owner", "me"])



class LeaseFilesPreservationTest(unittest.TestCase):
    """The lease layer never truncates or deletes a file it did not create (audit round 11)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.tmp.name)
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_a_file_at_the_old_fixed_temporary_name_survives_a_lease_write(self):
        planted = self.task_dir / "lease.json.tmp"
        planted.write_text("FOREIGN", encoding="utf-8")
        self.assertTrue(pe.acquire_lease(self.task_dir, "me").allowed)
        self.assertTrue(pe.heartbeat_lease(self.task_dir, "me", 600).allowed)
        self.assertEqual(planted.read_text(), "FOREIGN")
        self.assertEqual(list(self.task_dir.glob(".lease.*.tmp")), [])

    def test_the_lock_is_removed_on_exit_unless_someone_replaced_it(self):
        lock = self.task_dir / "lease.lock"
        with pe._lease_lock(self.task_dir):
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())
        if sys.platform == "win32":
            return  # replacing a held (open) lock is itself impossible on native Windows
        with pe._lease_lock(self.task_dir):
            lock.unlink()
            lock.write_text("theirs", encoding="utf-8")
        self.assertEqual(lock.read_text(), "theirs")

    def test_the_windows_release_order_closes_before_deleting(self):
        # Round 12: Windows cannot delete an open file, so the lock is closed first there. The
        # branch is exercised on POSIX by pretending to be Windows; the file still goes, exactly
        # once, and a replacement still stays.
        lock = self.task_dir / "lease.lock"
        order = []
        real_close, real_unlink = pe.os.close, Path.unlink

        def close(fd):
            order.append("close")
            real_close(fd)

        def unlink(path, *args, **kwargs):
            if path.name == "lease.lock":
                order.append("unlink")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(pe.os, "name", "nt"), \
                mock.patch.object(pe.os, "close", side_effect=close), \
                mock.patch.object(Path, "unlink", unlink):
            with pe._lease_lock(self.task_dir):
                pass
            self.assertFalse(lock.exists())
            self.assertEqual(order, ["close", "unlink"])
            if sys.platform == "win32":
                return  # replacing a held (open) lock is itself impossible on native Windows
            with pe._lease_lock(self.task_dir):
                lock.unlink()
                lock.write_text("theirs", encoding="utf-8")
            self.assertEqual(lock.read_text(), "theirs")
            self.assertEqual(order.count("close"), 2)

if __name__ == "__main__":
    unittest.main()
