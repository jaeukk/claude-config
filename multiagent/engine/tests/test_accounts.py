"""Tests for engine/accounts.py: probe parsing, classification, supervised runs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import accounts  # noqa: E402


def limit(kind, percent, resets="2026-09-20T00:00:00Z", display=None):
    entry = {"kind": kind, "percent": percent, "resets_at": resets}
    if display:
        entry["scope"] = {"model": {"display_name": display}}
    return entry


class ParseLimitsTest(unittest.TestCase):
    def test_under_cutoff_is_available(self):
        result = accounts.parse_limits({"limits": [limit("session", 6), limit("weekly_all", 40)]})
        self.assertEqual(result.state, "available")
        self.assertTrue(result.available)
        self.assertEqual([w["kind"] for w in result.windows], ["session", "weekly_all"])

    def test_session_over_cutoff_is_exhausted(self):
        result = accounts.parse_limits({"limits": [limit("session", 96), limit("weekly_all", 10)]})
        self.assertEqual(result.state, "exhausted")
        self.assertFalse(result.available)
        self.assertTrue(result.reason.startswith("exhausted: session"))

    def test_cutoff_is_inclusive(self):
        self.assertFalse(accounts.parse_limits({"limits": [limit("session", 95)]}).available)
        self.assertTrue(accounts.parse_limits({"limits": [limit("session", 94.9)]}).available)

    def test_scoped_window_applies_only_to_its_model(self):
        payload = {"limits": [limit("session", 1), limit("weekly_fable", 99, display="Fable")]}
        self.assertEqual(accounts.parse_limits(payload, model="claude-sonnet-5").state, "available")
        self.assertEqual(accounts.parse_limits(payload, model="claude-fable-5-1").state, "exhausted")
        # Unknown requested model: scoped windows count (conservative).
        self.assertEqual(accounts.parse_limits(payload, model=None).state, "exhausted")

    def test_idle_window_counts_as_zero(self):
        # A never-used account reports its window with no reset time: maximally available.
        payload = {"limits": [{"kind": "session", "percent": 0, "resets_at": None}]}
        result = accounts.parse_limits(payload)
        self.assertEqual(result.state, "available")
        self.assertEqual(result.windows, [{"kind": "session", "percent": 0.0, "resets_at": None}])

    def test_used_window_without_reset_time_is_unknown(self):
        payload = {"limits": [limit("session", 3), {"kind": "weekly_all", "percent": 40, "resets_at": None}]}
        result = accounts.parse_limits(payload)
        self.assertEqual(result.state, "unknown")
        self.assertIn("no reset time", result.reason)

    def test_malformed_percent_is_unknown(self):
        for bad in ("7", None, 140, True):
            result = accounts.parse_limits({"limits": [{"kind": "session", "percent": bad, "resets_at": "x"}]})
            self.assertEqual(result.state, "unknown")
            self.assertFalse(result.known)
            # Unknown still routes here: absence of evidence is not exhaustion.
            self.assertTrue(result.available)
            self.assertIn("probe_unknown", result.reason)

    def test_empty_or_missing_limits_is_unknown(self):  # noqa: D102
        self.assertIn("probe_unknown", accounts.parse_limits({}).reason)
        self.assertIn("probe_unknown", accounts.parse_limits({"limits": []}).reason)
        self.assertIn("probe_unknown", accounts.parse_limits({"limits": [{"kind": "other"}]}).reason)


class ClassifyTest(unittest.TestCase):
    def test_429_status_is_rate_limited(self):
        self.assertEqual(accounts.classify(1, {"type": "result", "is_error": True, "api_error_status": 429}), "rate_limited")

    def test_error_text_mentioning_limit_is_rate_limited(self):
        env = {"type": "result", "is_error": True, "result": "You have hit your usage limit until 3pm"}
        self.assertEqual(accounts.classify(1, env), "rate_limited")

    def test_other_errors_are_error(self):
        env = {"type": "result", "is_error": True, "result": "There's an issue with the selected model"}
        self.assertEqual(accounts.classify(1, env), "error")
        self.assertEqual(accounts.classify(1, {"type": "result", "is_error": False}), "error")

    def test_success(self):
        self.assertEqual(accounts.classify(0, {"type": "result", "is_error": False, "result": "OK"}), "ok")
        self.assertEqual(accounts.classify(0, None), "ok")

    def test_no_envelope(self):
        self.assertEqual(accounts.classify(1, None, "HTTP 429 rate_limit_error"), "rate_limited")
        self.assertEqual(accounts.classify(1, None, "boom"), "unknown")


class ReplayTest(unittest.TestCase):
    def test_plain_print_run_may_replay(self):
        self.assertTrue(accounts.replay_allowed(["-p", "hello"]))

    def test_continuing_or_acting_runs_may_not(self):
        self.assertFalse(accounts.replay_allowed(["-p", "--resume", "abc", "x"]))
        self.assertFalse(accounts.replay_allowed(["-p", "--permission-mode", "acceptEdits", "x"]))
        self.assertFalse(accounts.replay_allowed(["hello"]))


class ParseEnvelopeTest(unittest.TestCase):
    def test_last_result_line_wins(self):
        out = 'warning line\n{"type":"system"}\n{"type":"result","is_error":false,"result":"A"}\n'
        self.assertEqual(accounts.parse_envelope(out)["result"], "A")
        self.assertIsNone(accounts.parse_envelope("no json here"))


class RunSupervisedTest(unittest.TestCase):
    def test_child_gets_config_dir_and_parent_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "dir with spaces"
            config_dir.mkdir()
            script = (
                "import json, os; print(json.dumps({'type': 'result', 'is_error': False, "
                "'result': os.environ['CLAUDE_CONFIG_DIR']}))"
            )
            before = dict(os.environ)
            attempt = accounts.run_supervised(
                [sys.executable, "-c", script], config_dir, "team", "opus"
            )
            self.assertEqual(os.environ, before)
            self.assertEqual(attempt.classification, "ok")
            self.assertEqual(attempt.envelope["result"], str(config_dir))
            self.assertEqual(attempt.account, "team")


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "claude-team"
        self.config.mkdir()
        (self.config / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "tok"}}), encoding="utf-8"
        )
        self.cache = Path(self.tmp.name) / "cache"

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_uses_cache_within_ttl(self):
        payload = {"limits": [limit("session", 10), limit("weekly_all", 20)]}
        with mock.patch.object(accounts, "fetch_usage", return_value=payload) as fetch:
            first = accounts.probe(self.config, model="claude-sonnet-5", cache_dir=self.cache, now=1000.0)
            second = accounts.probe(self.config, model="claude-sonnet-5", cache_dir=self.cache, now=1030.0)
            stale = accounts.probe(self.config, model="claude-sonnet-5", cache_dir=self.cache, now=1100.0)
        self.assertTrue(first.available and second.available and stale.available)
        self.assertEqual(first.state, "available")
        self.assertIn("(cached)", second.reason)
        self.assertEqual(fetch.call_count, 2)

    def test_http_error_is_unknown_not_exhausted(self):
        import urllib.error
        error = urllib.error.HTTPError(accounts.USAGE_URL, 401, "unauthorized", {}, None)
        with mock.patch.object(accounts, "fetch_usage", side_effect=error):
            result = accounts.probe(self.config, cache_dir=self.cache, now=5.0)
        # Unreadable, not exhausted: the run itself reports whether the account works.
        self.assertEqual(result.state, "unknown")
        self.assertFalse(result.known)
        self.assertEqual(result.reason, "probe_unknown: HTTP 401")

    def _prime(self, percent, now=1000.0):
        """Store one successful reading at ``now``."""
        payload = {"limits": [limit("session", percent)]}
        with mock.patch.object(accounts, "fetch_usage", return_value=payload):
            return accounts.probe(self.config, cache_dir=self.cache, now=now)

    def _limited(self, code=429):
        import urllib.error
        return mock.patch.object(
            accounts, "fetch_usage",
            side_effect=urllib.error.HTTPError(accounts.USAGE_URL, code, "x", {}, None),
        )

    def test_endpoint_429_reuses_a_recent_reading(self):
        self._prime(12)
        with self._limited():
            warm = accounts.probe(self.config, cache_dir=self.cache, now=1100.0)
        self.assertEqual(warm.state, "available")
        self.assertIn("stale", warm.reason)
        self.assertIn("HTTP 429", warm.reason)

    def test_cold_429_is_unknown_and_still_routes_here(self):
        with self._limited():
            cold = accounts.probe(self.config, cache_dir=self.cache, now=5.0)
        self.assertEqual(cold.state, "unknown")
        self.assertTrue(cold.available)  # bias toward the account we meant to spend

    def test_failure_starts_a_cooldown_that_suppresses_probes(self):
        self._prime(12)
        with self._limited() as limited:
            accounts.probe(self.config, cache_dir=self.cache, now=1100.0)
            accounts.probe(self.config, cache_dir=self.cache, now=1101.0)
            accounts.probe(self.config, cache_dir=self.cache, now=1102.0)
            self.assertEqual(limited.call_count, 1)  # one request, then cooldown

    def test_backoff_lengthens_with_consecutive_failures(self):
        with self._limited():
            accounts.probe(self.config, cache_dir=self.cache, now=0.0)
            accounts.probe(self.config, cache_dir=self.cache, now=accounts.PROBE_BACKOFF_SECONDS[0] + 1)
        entry = json.loads(next(self.cache.iterdir()).read_text())
        self.assertEqual(entry["failures"], 2)
        self.assertGreater(entry["cooldown_until"], accounts.PROBE_BACKOFF_SECONDS[1])

    def test_auth_failure_is_never_covered_by_a_stale_reading(self):
        self._prime(12)
        with self._limited(401):
            result = accounts.probe(self.config, cache_dir=self.cache, now=1100.0)
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.reason, "probe_unknown: HTTP 401")

    def test_known_exhaustion_outlives_a_stale_available_reading(self):
        # Exhausted only improves at reset, so it keeps deciding; an available reading
        # decays because another session can spend that headroom.
        self._prime(99)
        with self._limited():
            late = accounts.probe(self.config, cache_dir=self.cache, now=1000.0 + accounts.STALE_TTL_SECONDS * 3)
        self.assertEqual(late.state, "exhausted")
        self._prime(10, now=9000.0)
        with self._limited():
            decayed = accounts.probe(self.config, cache_dir=self.cache, now=9000.0 + accounts.STALE_TTL_SECONDS + 1)
        self.assertEqual(decayed.state, "unknown")

    def test_reading_expires_at_its_window_reset(self):
        import datetime as dt
        reset = dt.datetime(2026, 9, 20, tzinfo=dt.UTC)
        self._prime(99)  # resets_at 2026-09-20
        with self._limited():
            after = accounts.probe(self.config, cache_dir=self.cache, now=reset.timestamp() + 60)
        self.assertEqual(after.state, "unknown")

    def test_one_reading_serves_every_model(self):
        payload = {"limits": [limit("session", 12)]}
        with mock.patch.object(accounts, "fetch_usage", return_value=payload) as fetch:
            accounts.probe(self.config, model="claude-sonnet-5", cache_dir=self.cache, now=1000.0)
            accounts.probe(self.config, model="claude-haiku-4-5", cache_dir=self.cache, now=1010.0)
        self.assertEqual(fetch.call_count, 1)

    def test_a_worker_run_429_outranks_any_probe(self):
        self._prime(2)
        accounts.note_run_rate_limited(self.config, cache_dir=self.cache, now=1010.0)
        blocked = accounts.probe(self.config, cache_dir=self.cache, now=1020.0)
        self.assertEqual(blocked.state, "exhausted")
        self.assertIn("worker run hit 429", blocked.reason)
        lapsed = accounts.probe(self.config, cache_dir=self.cache, now=1010.0 + accounts.RUN_LIMIT_SECONDS + 1)
        self.assertEqual(lapsed.state, "unknown")  # endpoint still cooling down

    def test_missing_credentials_is_unknown(self):
        result = accounts.probe(Path(self.tmp.name) / "nowhere", cache_dir=self.cache, now=5.0)
        self.assertEqual(result.state, "unknown")
        self.assertIn("credentials unreadable", result.reason)

    def test_invalid_cutoff_rejected(self):
        with self.assertRaises(ValueError):
            accounts.probe(self.config, max_percent=0, cache_dir=self.cache)


if __name__ == "__main__":
    unittest.main()
