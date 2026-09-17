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


if __name__ == "__main__":
    unittest.main()
