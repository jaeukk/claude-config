"""Tests for account-aware dispatch in engine/policy_engine.py against the real policy."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

import accounts  # noqa: E402
import policy_engine as pe  # noqa: E402


class ResolveBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def test_policy_validates(self):
        errors, _ = pe.validate_policy(self.bundle)
        self.assertEqual(errors, [])

    def test_verifier_for_codex_author_prefers_team(self):
        decision = pe.resolve_binding(self.bundle, "verifier", author_family="codex")
        self.assertEqual(decision.details["backend"], "claude-mid-team")
        self.assertEqual(decision.details["account"], "team")

    def test_native_spawn_excludes_team(self):
        decision = pe.resolve_binding(self.bundle, "verifier", author_family="codex", exclude_account_bound=True)
        self.assertEqual(decision.details["backend"], "claude-mid")

    def test_probe_runs_only_when_a_team_backend_would_win(self):
        # runner resolves to codex-fast, so nothing should be probed for it.
        with mock.patch.object(accounts, "probe") as probe:
            binding, reason = pe._resolve_with_account(self.bundle, "runner", False, conductor_host="claude-code")
        probe.assert_not_called()
        self.assertEqual(binding.details["backend"], "codex-fast")
        self.assertEqual(reason, "not a team candidate")

    def test_unknown_quota_keeps_the_team_backend(self):
        with mock.patch.object(accounts, "probe", return_value=accounts.Availability("unknown", "probe_unknown: HTTP 429")):
            binding, reason = pe._resolve_with_account(self.bundle, "bulk_worker", False)
        self.assertEqual(binding.details["backend"], "claude-fast-team")
        self.assertIn("unknown", reason)

    def test_exhausted_quota_falls_back_to_private(self):
        with mock.patch.object(accounts, "probe", return_value=accounts.Availability("exhausted", "exhausted: session at 97%")):
            binding, reason = pe._resolve_with_account(self.bundle, "bulk_worker", False)
        self.assertEqual(binding.details["backend"], "claude-fast")
        self.assertIn("exhausted", reason)

    def test_exhausted_team_falls_to_private(self):
        decision = pe.resolve_binding(self.bundle, "verifier", author_family="codex", availability={"team": False})
        self.assertEqual(decision.details["backend"], "claude-mid")
        available = pe.resolve_binding(self.bundle, "verifier", author_family="codex", availability={"team": True})
        self.assertEqual(available.details["backend"], "claude-mid-team")

    def test_bulk_worker_prefers_team(self):
        for author in ("claude", "codex"):
            decision = pe.resolve_binding(self.bundle, "bulk_worker", author_family=author)
            self.assertEqual(decision.details["backend"], "claude-fast-team")
            self.assertEqual(decision.details["account"], "team")

    def test_runner_keeps_codex_first(self):
        # codex-fast leads on the recorded benchmark (equal accuracy, 26x fewer tokens);
        # the team account is the Claude-side second choice, private the third.
        self.assertEqual(pe.resolve_binding(self.bundle, "runner").details["backend"], "codex-fast")
        claude_only = pe.resolve_binding(self.bundle, "runner", required_family="claude")
        self.assertEqual(claude_only.details["backend"], "claude-fast-team")
        self.assertEqual(
            pe.resolve_binding(
                self.bundle, "runner", required_family="claude", availability={"team": False}
            ).details["backend"],
            "claude-fast",
        )

    def test_bulk_worker_falls_back_in_list_order(self):
        exhausted = {"team": False}
        self.assertEqual(
            pe.resolve_binding(self.bundle, "bulk_worker", availability=exhausted).details["backend"],
            "claude-fast",
        )
        # Native Task-tool children never reach team either.
        self.assertEqual(
            pe.resolve_binding(self.bundle, "bulk_worker", exclude_account_bound=True).details["backend"],
            "claude-fast",
        )

    def test_review_independence_unchanged(self):
        self.assertEqual(pe.resolve_binding(self.bundle, "critic", author_family="claude").details["family"], "codex")
        self.assertEqual(pe.resolve_binding(self.bundle, "verifier", author_family="claude").details["family"], "codex")

    def test_authorize_native_spawn_skips_team(self):
        task = {
            "schema_version": 1, "task_id": "t", "status": "active", "target_repo": str(ROOT),
            "write_scope": [], "roles_plan": ["verifier"], "author_family": "codex",
            "conductor": {"host": "claude-code", "backend": "claude-frontier", "lease_owner": "me"},
            "dispatch": {"current_role": "verifier", "active_workers": 0}, "approvals": {"user": []},
        }
        errors, _ = pe.validate_task(self.bundle, task)
        self.assertEqual(errors, [], errors)
        native = pe.authorize_action(self.bundle, task, {"kind": "spawn_worker", "role": "verifier", "native": True})
        cli = pe.authorize_action(self.bundle, task, {"kind": "spawn_worker", "role": "verifier"})
        self.assertEqual(native.details["backend"], "claude-mid")
        self.assertEqual(cli.details["backend"], "claude-mid-team")


class ValidatePolicyAccountsTest(unittest.TestCase):
    def _bundle_with(self, alias, backend):
        bundle = pe.load_policy(ROOT)
        bundle.backends[alias] = backend
        return bundle

    def test_team_without_config_dir_is_an_error(self):
        bundle = self._bundle_with("claude-x", {**pe.load_policy(ROOT).backends["claude-mid"], "account": "team", "config_dir": None})
        errors, _ = pe.validate_policy(bundle)
        self.assertTrue(any("team-bound but has no config_dir" in e for e in errors), errors)

    def test_account_on_non_claude_host_is_an_error(self):
        bundle = self._bundle_with("codex-x", {**pe.load_policy(ROOT).backends["codex-mid"], "account": "team", "config_dir": "~/x"})
        errors, _ = pe.validate_policy(bundle)
        self.assertTrue(any("not a claude-code host" in e for e in errors), errors)

    def test_unknown_account_label_is_an_error(self):
        bundle = self._bundle_with("claude-y", {**pe.load_policy(ROOT).backends["claude-mid"], "account": "guest"})
        errors, _ = pe.validate_policy(bundle)
        self.assertTrue(any("unknown account" in e for e in errors), errors)


class ClaudeCliTest(unittest.TestCase):
    def test_env_and_json_output(self):
        backend = {"model": "claude-sonnet-5", "effort": "medium", "config_dir": "~/.claude-team"}
        spec = pe._claude_cli(backend)
        self.assertEqual(spec.env, {"CLAUDE_CONFIG_DIR": str(Path("~/.claude-team").expanduser())})
        self.assertIn("--output-format", spec.args)
        self.assertEqual(spec.args[spec.args.index("--tools") + 1], "Read,Grep,Glob")

    def test_no_config_dir_means_no_env(self):
        self.assertEqual(pe._claude_cli({"model": "m", "effort": "low"}).env, {})

    def test_only_the_native_launcher_exists(self):
        # The WSL launcher was retired in 1.4.0 (never used).
        backend = {"host": "claude-code", "model": "m", "effort": "low", "config_dir": "~/.claude-team"}
        with self.assertRaises(ValueError):
            pe.build_worker_command(backend, "wsl", Path("."))


class RunWorkerTest(unittest.TestCase):
    def test_env_merged_and_parent_untouched(self):
        backend = {"host": "claude-code", "model": "claude-sonnet-5", "effort": "medium",
                   "account": "team", "config_dir": "/tmp/team dir", "backend": "claude-mid-team", "family": "claude"}
        spec = pe._claude_cli(backend)
        seen = {}

        def fake_run(command, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(
                command, 0, stdout='{"type":"result","is_error":false,"result":"fine","api_error_status":null}\n', stderr=""
            )

        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("do the thing", encoding="utf-8")
            before = dict(os.environ)
            with mock.patch.object(pe.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
                attempt = pe._run_worker(["claude", *spec.args], spec, {"task_id": "t"}, "verifier", brief, Path(tmp), backend)
        self.assertEqual(os.environ, before)
        self.assertEqual(seen["env"]["CLAUDE_CONFIG_DIR"], "/tmp/team dir")
        self.assertTrue(seen["capture_output"])
        self.assertEqual(attempt.classification, "ok")
        self.assertEqual(attempt.account, "team")
        self.assertEqual(attempt.model, "claude-sonnet-5")

    def test_rate_limited_envelope_classified(self):
        backend = {"host": "claude-code", "model": "m", "effort": "low", "account": "team",
                   "config_dir": "/tmp/t", "backend": "b", "family": "claude"}
        spec = pe._claude_cli(backend)

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout='{"type":"result","is_error":true,"api_error_status":429,"result":"limit"}\n', stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("x", encoding="utf-8")
            with mock.patch.object(pe.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
                attempt = pe._run_worker(["claude"], spec, {"task_id": "t"}, "verifier", brief, Path(tmp), backend)
        self.assertEqual(attempt.classification, "rate_limited")
        self.assertEqual(attempt.as_event()["api_error_status"], 429)


class EmitAttemptTest(unittest.TestCase):
    def test_only_the_surviving_attempt_is_written(self):
        failed = accounts.Attempt("team", "/t", "m", 1, "rate_limited", {"type": "result", "result": "limit"}, "raw-failure", "")
        good = accounts.Attempt("private", "/p", "m", 0, "ok", {"type": "result", "result": "the answer"}, "raw", "")
        import io
        out = io.StringIO()
        with mock.patch.object(pe.sys, "stdout", out), mock.patch.object(pe.sys, "stderr", io.StringIO()):
            pe._emit_attempt(good)
        self.assertEqual(out.getvalue().strip(), "the answer")
        self.assertNotIn("limit", out.getvalue())
        self.assertEqual(failed.classification, "rate_limited")  # held, never emitted


if __name__ == "__main__":
    unittest.main()
