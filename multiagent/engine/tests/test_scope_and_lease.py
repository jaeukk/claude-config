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


def _contract(target_repo: Path) -> dict:
    """A minimal active contract whose write_scope is ``docs/**`` in ``target_repo``."""
    return {
        "schema_version": 1, "task_id": "t1", "status": "active", "target_repo": str(target_repo),
        "write_scope": ["docs/**"], "roles_plan": ["implementer", "critic"], "audit_cycles": 1,
        "author_family": "claude", "approvals": {"user": []}, "dispatch": {"current_role": None},
        "conductor": {"host": "claude-code", "backend": "claude-frontier", "lease_owner": "me"},
    }


class OwnTaskFolderWriteTest(unittest.TestCase):
    """A conductor may write ordinary files in its own task folder; engine state stays refused (bench8)."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.repo = base / "repo"
        self.task_dir = base / "multiagent" / "tasks" / "t1"
        self.task_dir.mkdir(parents=True)
        self.repo.mkdir()
        self.task = _contract(self.repo)

    def write(self, path, actor="conductor", task_dir="own"):
        """Authorize one write; ``task_dir='own'`` passes this task's folder, as the hook does."""
        return pe.authorize_action(self.bundle, self.task, {"kind": "write", "actor_role": actor, "path": str(path)},
                                   task_dir=self.task_dir if task_dir == "own" else task_dir)

    def test_ordinary_files_in_the_own_folder_are_allowed(self):
        for name in ("critic-brief.md", "workers/critic/brief-round1.md", "notes/result.md"):
            with self.subTest(name):
                self.assertTrue(self.write(self.task_dir / name).allowed)

    def test_without_the_task_folder_nothing_changes(self):
        self.assertFalse(self.write(self.task_dir / "critic-brief.md", task_dir=None).allowed)

    def test_engine_state_in_the_own_folder_stays_refused(self):
        for name in ("task.yaml", "lease.json", "lease.lock", "lease.stale.123.json", "events.ndjson",
                     pe.OBSERVED_AUTHOR_FILE, "outputs/r.md", "writes/x", ".task-initial.abc.json",
                     ".lease.x.tmp", "workers/.hidden", "TASK.YAML", "Lease.Stale.1.json", "OUTPUTS/r.md"):
            with self.subTest(name):
                self.assertFalse(self.write(self.task_dir / name).allowed)
        self.assertFalse(self.write(self.task_dir).allowed)

    def test_another_task_and_non_conductors_are_refused(self):
        sibling = self.task_dir.parent / "t2"
        sibling.mkdir()
        self.assertFalse(self.write(sibling / "brief.md").allowed)
        self.assertFalse(self.write(self.task_dir / "brief.md", actor="implementer").allowed)
        self.assertFalse(self.write(self.task_dir / "brief.md", actor="critic").allowed)

    def test_a_symlink_out_of_the_folder_does_not_qualify(self):
        outside = Path(self.tmp.name).resolve() / "elsewhere"
        outside.mkdir()
        try:
            (self.task_dir / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertFalse(self.write(self.task_dir / "link" / "brief.md").allowed)

    def test_the_scope_rule_still_applies_elsewhere(self):
        self.assertTrue(self.write(self.repo / "docs" / "a.md").allowed)
        self.assertFalse(self.write(self.repo / "src" / "a.py").allowed)


class ReleaseClearsActivePointerTest(unittest.TestCase):
    """``release-lease`` removes ``tasks/.active-task`` only when it names the released task (bench8)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = Path(self.tmp.name).resolve() / "multiagent"
        self.tasks = self.install / "tasks"
        self.task_dir = self.tasks / "t1"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.yaml").write_text("{}", encoding="utf-8")
        self.pointer = self.tasks / ".active-task"
        patcher = mock.patch.object(pe, "INSTALLATION_ROOT", self.install)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertTrue(pe.acquire_lease(self.task_dir, "me").allowed)

    def test_a_pointer_naming_this_task_is_removed(self):
        self.pointer.write_text("t1\n", encoding="utf-8")
        decision = pe.release_lease(self.task_dir, "me")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details, {"active_task_cleared": True})
        self.assertFalse(self.pointer.exists())

    def test_a_pointer_naming_another_task_or_none_is_left_alone(self):
        self.pointer.write_text("t2\n", encoding="utf-8")
        self.assertEqual(pe.release_lease(self.task_dir, "me").details, {"active_task_cleared": False})
        self.assertEqual(self.pointer.read_text(encoding="utf-8"), "t2\n")
        self.pointer.unlink()
        self.assertTrue(pe.acquire_lease(self.task_dir, "me").allowed)
        self.assertEqual(pe.release_lease(self.task_dir, "me").details, {"active_task_cleared": False})

    def test_a_refused_release_keeps_the_pointer(self):
        self.pointer.write_text("t1\n", encoding="utf-8")
        self.assertFalse(pe.release_lease(self.task_dir, "someone-else").allowed)
        self.assertTrue(self.pointer.exists())
        lease = pe.load_document(self.task_dir / "lease.json")
        lease["active_workers"] = 1
        (self.task_dir / "lease.json").write_text(pe.json.dumps(lease), encoding="utf-8")
        self.assertFalse(pe.release_lease(self.task_dir, "me").allowed)
        self.assertTrue(self.pointer.exists())

    def test_a_task_outside_the_installation_never_touches_its_pointer(self):
        elsewhere = Path(self.tmp.name).resolve() / "other" / "t1"
        elsewhere.mkdir(parents=True)
        (elsewhere / "task.yaml").write_text("{}", encoding="utf-8")
        self.assertTrue(pe.acquire_lease(elsewhere, "me").allowed)
        self.pointer.write_text("t1\n", encoding="utf-8")
        self.assertEqual(pe.release_lease(elsewhere, "me").details, {"active_task_cleared": False})
        self.assertTrue(self.pointer.exists())

    def test_a_symlinked_pointer_is_left_alone(self):
        target = Path(self.tmp.name).resolve() / "pointer-target"
        target.write_text("t1\n", encoding="utf-8")
        try:
            self.pointer.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(pe.release_lease(self.task_dir, "me").details, {"active_task_cleared": False})
        self.assertTrue(self.pointer.is_symlink() and target.exists())


if __name__ == "__main__":
    unittest.main()
