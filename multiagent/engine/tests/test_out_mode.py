"""Tests for ``dispatch-worker --out``: success predicate, guards, and publication."""

from __future__ import annotations

import fnmatch
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

import accounts  # noqa: E402
import policy_engine as pe  # noqa: E402


def claude_attempt(exit_code=0, envelope=None, result_text=None):
    """One finished Claude attempt, envelope and captured result spelled out."""
    return accounts.Attempt(
        "team", "/t", "m", exit_code, "ok", envelope, "", "", result_text=result_text
    )


def codex_attempt(exit_code=0, result_text=None):
    """One finished Codex attempt; ``result_text`` is what its ``-o`` file held."""
    return accounts.Attempt(
        "private", "", "m", exit_code, "ok", None, "", "", result_text=result_text
    )


def envelope(**overrides):
    """A complete successful Claude result envelope, before any overrides."""
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": "the document", **overrides}


class AttemptSucceededTest(unittest.TestCase):
    def test_complete_claude_envelope_succeeds(self):
        self.assertTrue(accounts.attempt_succeeded(claude_attempt(envelope=envelope()), "claude-code"))

    def test_nonzero_exit_never_succeeds(self):
        for host, attempt in (
            ("claude-code", claude_attempt(1, envelope())),
            ("codex", codex_attempt(1, "text")),
        ):
            with self.subTest(host=host):
                self.assertFalse(accounts.attempt_succeeded(attempt, host))

    def test_exit_zero_without_an_envelope_fails(self):
        # classify() calls this `ok`; publication must not.
        attempt = claude_attempt(0, None)
        self.assertEqual(accounts.classify(0, None), "ok")
        self.assertFalse(accounts.attempt_succeeded(attempt, "claude-code"))

    def test_malformed_envelopes_fail(self):
        for name, payload in (
            ("missing is_error", {"type": "result", "subtype": "success", "result": "x"}),
            ("null is_error", envelope(is_error=None)),
            ("truthy-but-not-false is_error", envelope(is_error=0)),
            ("error flagged", envelope(is_error=True)),
            ("missing subtype", {"type": "result", "is_error": False, "result": "x"}),
            ("wrong subtype", envelope(subtype="error_max_turns")),
            ("wrong type", envelope(type="system")),
            ("missing result", {"type": "result", "subtype": "success", "is_error": False}),
            ("non-string result", envelope(result={"text": "x"})),
        ):
            with self.subTest(name):
                self.assertFalse(
                    accounts.attempt_succeeded(claude_attempt(0, payload), "claude-code")
                )

    def test_is_error_zero_is_not_false(self):
        # JSON `0` is falsy but is not `false`; a truncated envelope must not pass.
        self.assertFalse(accounts.attempt_succeeded(claude_attempt(0, envelope(is_error=0)), "claude-code"))

    def test_codex_needs_a_non_empty_result_file(self):
        self.assertTrue(accounts.attempt_succeeded(codex_attempt(0, "verdict"), "codex"))
        self.assertFalse(accounts.attempt_succeeded(codex_attempt(0, ""), "codex"))
        self.assertFalse(accounts.attempt_succeeded(codex_attempt(0, None), "codex"))

    def test_unknown_host_never_succeeds(self):
        self.assertFalse(accounts.attempt_succeeded(codex_attempt(0, "text"), "agy"))

    def test_claude_ignores_the_result_file_and_codex_ignores_the_envelope(self):
        # Each host has exactly one channel; borrowing the other's would let a run pass
        # on evidence its own CLI never produced.
        self.assertFalse(accounts.attempt_succeeded(claude_attempt(0, None, "text"), "claude-code"))
        self.assertFalse(accounts.attempt_succeeded(
            accounts.Attempt("p", "", "m", 0, "ok", envelope(), "", ""), "codex"
        ))


class ResolvePublicationPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "docs" / "note.md").write_text("old", encoding="utf-8")
        (self.repo / "tasks" / "t1").mkdir(parents=True)

    def allow(self, path):
        return pe.resolve_publication_path(self.repo, path)

    def test_plain_destination_is_allowed(self):
        decision = self.allow("docs/new.md")
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["path"], str(self.repo / "docs" / "new.md"))

    def test_nested_missing_directories_are_allowed(self):
        decision = self.allow("docs/a/b/c.md")
        self.assertTrue(decision.allowed, decision.reason)

    def test_existing_regular_file_may_be_overwritten(self):
        self.assertTrue(self.allow("docs/note.md").allowed)

    def test_directory_at_the_destination_is_refused(self):
        decision = self.allow("docs")
        self.assertFalse(decision.allowed)
        self.assertIn("not a regular file", decision.reason)

    def test_symlink_at_the_destination_is_refused(self):
        (self.repo / "docs" / "link.md").symlink_to(self.repo / "docs" / "note.md")
        decision = self.allow("docs/link.md")
        self.assertFalse(decision.allowed)
        self.assertIn("symlink", decision.reason)

    def test_symlinked_ancestor_is_refused(self):
        (self.repo / "alias").symlink_to(self.repo / "tasks" / "t1")
        decision = self.allow("alias/notes.md")
        self.assertFalse(decision.allowed)
        self.assertIn("symlink", decision.reason)

    def test_non_directory_ancestor_is_refused(self):
        decision = self.allow("docs/note.md/child.md")
        self.assertFalse(decision.allowed)
        self.assertIn("not a directory", decision.reason)

    def test_parent_traversal_is_refused_outright(self):
        for path in ("docs/../tasks/t1/task.yaml", "../escape.md", "docs/../../escape.md"):
            with self.subTest(path):
                decision = self.allow(path)
                self.assertFalse(decision.allowed)
                self.assertIn("'..'", decision.reason)

    def test_dot_and_empty_segments_are_refused_from_the_raw_string(self):
        # `Path` drops these before `.parts` is consulted, so the rule has to be read off
        # the string the caller actually passed.
        for path, segment in (
            ("docs/./note.md", "."), ("docs//note.md", ""), ("docs/", ""),
        ):
            with self.subTest(path):
                decision = self.allow(path)
                self.assertFalse(decision.allowed, path)
                self.assertIn(repr(segment), decision.reason)

    def test_outside_target_repo_is_refused(self):
        decision = self.allow(str(Path(self.tmp.name) / "elsewhere.md"))
        self.assertFalse(decision.allowed)
        self.assertIn("outside target_repo", decision.reason)

    def test_target_repo_itself_is_refused(self):
        self.assertFalse(self.allow(".").allowed)

    def test_reserved_task_state(self):
        for path in (
            "tasks/t1/task.yaml", "tasks/t1/lease.json", "tasks/t1/lease.lock",
            "tasks/t1/events.ndjson", "tasks/t1/observed-author.json",
            "tasks/t1/patches/x.json", "tasks/t1/patches/failed/x.json",
            "tasks/t1/outputs/x.json", "tasks/t1/reviews/x.json", "tasks/t1/applies/x.json",
            "tasks/.active-task",
        ):
            with self.subTest(path):
                decision = self.allow(path)
                self.assertFalse(decision.allowed, path)
                self.assertIn("reserved", decision.reason)

    def test_an_absolute_alias_of_a_reserved_path_is_refused(self):
        decision = self.allow(str(self.repo / "tasks" / "t1" / "task.yaml"))
        self.assertFalse(decision.allowed)
        self.assertIn("reserved", decision.reason)

    def test_engine_state_survives_a_moved_repository_boundary(self):
        # The escape the relative-prefix rule allowed: spell target_repo differently and
        # `tasks/` is no longer the first component, so the prefix never matches.
        outer = pe.resolve_publication_path(
            ROOT.parent, f"{ROOT.name}/tasks/t-absent/lease.json"
        )
        self.assertFalse(outer.allowed)
        self.assertIn("reserved", outer.reason)
        # A control through the same boundary: only engine state is reserved, not the repo.
        self.assertTrue(
            pe.resolve_publication_path(ROOT.parent, f"{ROOT.name}/notes/scratch.md").allowed
        )

    def test_the_task_directory_as_target_repo_still_protects_its_own_state(self):
        # The other escape: point target_repo at the task directory itself and every
        # reserved name becomes a bare first component.
        task_dir = self.repo / "tasks" / "t1"
        for path in ("lease.json", "task.yaml", "events.ndjson", "outputs/x.json"):
            with self.subTest(path):
                decision = pe.resolve_publication_path(task_dir, path, task_dir)
                self.assertFalse(decision.allowed, path)
                self.assertIn("reserved", decision.reason)
        self.assertTrue(pe.resolve_publication_path(task_dir, "notes.md", task_dir).allowed)

    def test_the_task_directory_is_reserved_through_a_symlinked_spelling(self):
        # Identity, not spelling: the same directory reached by another name is the same
        # engine state, so `realpath` is what the comparison is made against.
        task_dir = self.repo / "tasks" / "t1"
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(task_dir)
        decision = pe.resolve_publication_path(self.repo, "tasks/t1/lease.json", alias)
        self.assertFalse(decision.allowed)
        self.assertIn("reserved", decision.reason)

    def test_task_state_names_outside_a_task_directory_are_allowed(self):
        # The reserved rule is structural (`tasks/<id>/...`), not a ban on the names.
        self.assertTrue(self.allow("docs/lease.json").allowed)
        self.assertTrue(self.allow("tasks/t1/sub/task.yaml").allowed)

    def test_reserved_repository_metadata(self):
        for path in (
            ".git/config", "sub/.git/config", ".gitattributes", ".gitmodules", ".gitignore",
        ):
            with self.subTest(path):
                self.assertFalse(self.allow(path).allowed, path)

    def test_reserved_tool_configuration(self):
        for path in (
            ".claude/settings.json", "sub/.claude/settings.json", ".codex/config.toml",
            ".vscode/settings.json", ".mcp.json", "sub/.mcp.json",
            ".github/workflows/ci.yml",
        ):
            with self.subTest(path):
                self.assertFalse(self.allow(path).allowed, path)

    def test_every_dot_git_prefixed_component_is_reserved(self):
        # The rule is the `.git` prefix, not a list of known names, so `.github/` goes with
        # it -- losing `--out .github/PULL_REQUEST_TEMPLATE.md` is the accepted cost.
        for path in (
            ".github/PULL_REQUEST_TEMPLATE.md", ".github/workflows/ci.yml",
            ".gitlab-ci.yml", "sub/.gitkeep",
        ):
            with self.subTest(path):
                decision = self.allow(path)
                self.assertFalse(decision.allowed, path)
                self.assertIn("reserved", decision.reason)

    def test_installation_trees_are_reserved_when_the_repo_is_the_installation(self):
        for path in ("engine/x.md", "policy/x.json", "_shared/x.md", ".claude/x.json"):
            with self.subTest(path):
                decision = pe.resolve_publication_path(ROOT, path)
                self.assertFalse(decision.allowed, path)
        self.assertTrue(pe.resolve_publication_path(ROOT, "docs/scratch-note.md").allowed)

    def test_same_names_are_writable_in_an_unrelated_repository(self):
        (self.repo / "engine").mkdir()
        self.assertTrue(self.allow("engine/notes.md").allowed)


class StatePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task_dir = Path(self.tmp.name)

    def test_fixed_layout_is_created(self):
        path = pe._state_path(self.task_dir, "outputs", "a" * 32, ".json")
        self.assertEqual(path, self.task_dir / "outputs" / f"{'a' * 32}.json")
        self.assertTrue(path.parent.is_dir())

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            pe._state_path(self.task_dir, "workspaces", "a" * 32, ".json")

    def test_malformed_dispatch_ids_are_refused(self):
        for bad in ("", "short", "A" * 32, "g" * 32, "a" * 31, "a" * 33, "../" + "a" * 29):
            with self.subTest(bad), self.assertRaises(ValueError):
                pe._state_path(self.task_dir, "outputs", bad, ".json")

    def test_symlinked_state_directory_is_refused(self):
        elsewhere = self.task_dir / "elsewhere"
        elsewhere.mkdir()
        (self.task_dir / "outputs").symlink_to(elsewhere)
        with self.assertRaises(ValueError):
            pe._state_path(self.task_dir, "outputs", "b" * 32, ".json")

    def test_symlinked_state_file_is_refused(self):
        (self.task_dir / "outputs").mkdir()
        (self.task_dir / "outputs" / f"{'c' * 32}.json").symlink_to(self.task_dir / "target")
        with self.assertRaises(ValueError):
            pe._state_path(self.task_dir, "outputs", "c" * 32, ".json")

    def test_symlinked_task_directory_is_refused(self):
        real = Path(self.tmp.name) / "real-task"
        real.mkdir()
        alias = Path(self.tmp.name) / "alias-task"
        alias.symlink_to(real)
        with self.assertRaises(ValueError):
            pe._state_path(alias, "outputs", "e" * 32, ".json")
        self.assertFalse((real / "outputs").exists(), "nothing may be created through a link")

    def test_symlinked_task_directory_ancestor_is_refused(self):
        real = Path(self.tmp.name) / "real-tasks"
        (real / "t1").mkdir(parents=True)
        alias = Path(self.tmp.name) / "alias-tasks"
        alias.symlink_to(real)
        with self.assertRaises(ValueError):
            pe._state_path(alias / "t1", "outputs", "f" * 32, ".json")

    def test_fixed_name_state_files(self):
        events = pe._state_file(self.task_dir, "events.ndjson")
        self.assertEqual(events, self.task_dir / "events.ndjson")
        with self.assertRaises(ValueError):
            pe._state_file(self.task_dir, "arbitrary.json")
        (self.task_dir / "events.ndjson").symlink_to(self.task_dir / "elsewhere.ndjson")
        with self.assertRaises(ValueError):
            pe._state_file(self.task_dir, "events.ndjson")

    def test_extension_may_not_leave_the_directory(self):
        # A suffix that climbs out of `outputs/` entirely. One that collapses back inside
        # it is harmless, because everything in there is engine state either way.
        with self.assertRaises(ValueError):
            pe._state_path(self.task_dir, "outputs", "d" * 32, "/../../escaped.json")


class CodexResultFileTest(unittest.TestCase):
    def test_codex_gets_an_output_file_only_when_the_engine_will_read_it(self):
        text_mode = pe._codex_cli({"model": "gpt-x", "effort": "low"})
        self.assertIsNone(text_mode.result_file)
        self.assertNotIn("-o", text_mode.args)

        spec = pe._codex_cli({"model": "gpt-x", "effort": "low"}, capture_result=True)
        self.assertIsNotNone(spec.result_file)
        self.assertEqual(spec.args[spec.args.index("-o") + 1], str(spec.result_file))
        # Before the positional `-`, or codex would read the path as the prompt source.
        self.assertLess(spec.args.index("-o"), spec.args.index("-"))
        self.assertFalse(spec.result_file.exists(), "the builder must not create the file")

    def test_each_dispatch_gets_its_own_file(self):
        backend = {"model": "gpt-x", "effort": "low"}
        self.assertNotEqual(
            pe._codex_cli(backend, capture_result=True).result_file,
            pe._codex_cli(backend, capture_result=True).result_file,
        )

    def test_claude_has_no_result_file_either_way(self):
        for capture in (False, True):
            with self.subTest(capture_result=capture):
                self.assertIsNone(
                    pe._claude_cli({"model": "m", "effort": "low"}, capture_result=capture).result_file
                )

    def test_wsl_refuses_codex_only_when_a_result_file_is_needed(self):
        backend = {"host": "codex", "model": "m", "effort": "low"}
        with self.assertRaises(NotImplementedError):
            pe.build_worker_command(backend, "wsl", Path("."), capture_result=True)

    def test_wsl_text_mode_codex_still_builds(self):
        # The regression this guards: `-o` on every dispatch broke `--host wsl` text mode,
        # which has no result file to translate and previously worked.
        backend = {"host": "codex", "model": "m", "effort": "low"}
        converted = subprocess.CompletedProcess([], 0, stdout="/mnt/c/repo\n", stderr="")
        with mock.patch.object(pe.shutil, "which", return_value="/usr/bin/wsl.exe"), \
                mock.patch.object(pe.subprocess, "run", return_value=converted):
            command, spec = pe.build_worker_command(backend, "wsl", Path("/repo"), "runner")
        self.assertIsNone(spec.result_file)
        self.assertNotIn("-o", command)
        self.assertIn("--exec", command)

    def test_read_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.txt"
            self.assertIsNone(pe._read_result_file(missing))
            self.assertIsNone(pe._read_result_file(None))
            empty = Path(tmp) / "empty.txt"
            empty.write_bytes(b"")
            self.assertEqual(pe._read_result_file(empty), "")
            binary = Path(tmp) / "binary.txt"
            binary.write_bytes(b"\xff\xfe not utf-8")
            self.assertIsNone(pe._read_result_file(binary))
            good = Path(tmp) / "good.txt"
            good.write_text("réport\n", encoding="utf-8")
            self.assertEqual(pe._read_result_file(good), "réport\n")

    def test_run_worker_reads_then_removes_the_result_file(self):
        backend = {"host": "codex", "model": "m", "effort": "low", "backend": "codex-fast",
                   "family": "codex"}
        spec = pe._codex_cli(backend, capture_result=True)

        def fake_run(command, **kwargs):
            spec.result_file.write_text("the verdict", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("do it", encoding="utf-8")
            with mock.patch.object(pe.subprocess, "run", side_effect=fake_run):
                attempt = pe._run_worker(
                    ["codex", *spec.args], spec, {"task_id": "t"}, "runner", brief, Path(tmp), backend
                )
        self.assertEqual(attempt.result_text, "the verdict")
        self.assertFalse(spec.result_file.exists())

    def test_claude_result_text_comes_from_the_envelope(self):
        backend = {"host": "claude-code", "model": "m", "effort": "low", "backend": "claude-fast",
                   "family": "claude"}
        spec = pe._claude_cli(backend)

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps(envelope(result="body text")) + "\n", stderr="",
            )

        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("do it", encoding="utf-8")
            with mock.patch.object(pe.subprocess, "run", side_effect=fake_run):
                attempt = pe._run_worker(
                    ["claude", *spec.args], spec, {"task_id": "t"}, "runner", brief, Path(tmp), backend
                )
        self.assertEqual(attempt.result_text, "body text")
        self.assertTrue(accounts.attempt_succeeded(attempt, "claude-code"))


class OutDispatchTest(unittest.TestCase):
    """End-to-end ``--out`` dispatches with the worker process itself replaced."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.task_dir = self.repo / "tasks" / "t1"
        self.task_dir.mkdir(parents=True)
        (self.repo / "docs").mkdir()
        self.brief = self.task_dir / "brief.md"
        self.brief.write_text("summarize the thing", encoding="utf-8")
        self.contract_path = self.task_dir / "task.yaml"
        self.write_contract()
        lease = pe.acquire_lease(self.task_dir, "me")
        self.assertTrue(lease.allowed, lease.reason)

    def write_contract(self, role="runner", write_scope=("docs", "tasks")):
        """Write a valid contract that plans ``role`` and scopes writes to ``write_scope``."""
        self.contract_path.write_text(json.dumps({
            "schema_version": 1, "task_id": "t1", "status": "active",
            "target_repo": str(self.repo), "write_scope": list(write_scope),
            "roles_plan": [role], "approvals": {"user": []},
            "conductor": {"host": "claude-code", "backend": "claude-frontier",
                          "lease_owner": "me"},
            "dispatch": {"current_role": role, "active_workers": 0},
        }, indent=2), encoding="utf-8")

    def dispatch_write(self, write, out=None, attempt=None, role="implementer"):
        """Run one `--write` dispatch with the worker process replaced."""
        # The default fixture contract plans `runner`; a write dispatch needs a role that may
        # write, and a lease it owns.
        self.write_contract(role=role)
        pe.acquire_lease(self.task_dir, "me", 600)
        contract = pe.load_document(self.contract_path)
        backend = {"backend": "claude-core-team", "host": "claude-code", "family": "claude",
                   "model": "claude-opus-5", "effort": "high", "account": "team",
                   "config_dir": "/tmp/team"}
        spec = pe._claude_cli(backend)
        runner = mock.Mock(return_value=attempt if attempt is not None
                           else claude_attempt(0, envelope(), "done"))
        with mock.patch.object(pe, "_run_worker", runner), \
                mock.patch.object(pe, "build_worker_command", return_value=(["claude"], spec)), \
                mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", backend), "stub")), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.dispatch_worker(
                self.bundle, contract, role, self.brief, "native", False,
                None, self.task_dir, self.task_dir, out, self.contract_path, 0, write,
            )
        return code, runner

    def dispatch(self, out, attempt=None, role="runner", run=None, backend=None, min_bytes=0):
        """Run one dispatch with the worker process replaced, returning (code, mock)."""
        contract = pe.load_document(self.contract_path)
        backend = backend or {"backend": "codex-fast", "host": "codex", "family": "codex",
                              "model": "gpt-x", "effort": "low"}
        spec = pe._codex_cli(backend, capture_result=out is not None)
        runner = mock.Mock(
            side_effect=run,
            return_value=attempt if attempt is not None else codex_attempt(0, "published body"),
        )
        with mock.patch.object(pe, "_run_worker", runner), \
                mock.patch.object(pe, "build_worker_command", return_value=(["codex"], spec)), \
                mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", backend), "stubbed")), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.dispatch_worker(
                self.bundle, contract, role, self.brief, "native", False,
                None, self.task_dir, self.task_dir, out, self.contract_path, min_bytes,
            )
        return code, runner

    def records(self):
        """Every provenance record this task directory holds."""
        outputs = self.task_dir / "outputs"
        return sorted(outputs.glob("*.json")) if outputs.exists() else []

    def attempt_events(self):
        """Every ``worker_attempt`` event appended to this task's event stream."""
        stream = self.task_dir / "events.ndjson"
        if not stream.exists():
            return []
        events = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
        return [event for event in events if event.get("type") == "worker_attempt"]

    def test_successful_dispatch_publishes_snapshot_destination_and_record(self):
        code, runner = self.dispatch("docs/deep/nested/summary.md")
        self.assertEqual(code, 0)
        runner.assert_called_once()
        destination = self.repo / "docs" / "deep" / "nested" / "summary.md"
        self.assertEqual(destination.read_text(encoding="utf-8"), "published body")
        self.assertTrue(destination.parent.is_dir())
        records = self.records()
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["destination"], str(destination))
        snapshot = Path(record["snapshot"])
        self.assertEqual(snapshot.read_bytes(), b"published body")
        self.assertEqual(snapshot.parent, self.task_dir / "outputs")
        self.assertEqual(record["bytes"], len(b"published body"))
        import hashlib
        self.assertEqual(record["sha256"], hashlib.sha256(b"published body").hexdigest())
        self.assertEqual(
            [record["role"], record["backend"], record["family"], record["attempt"]],
            ["runner", "codex-fast", "codex", 1],
        )
        self.assertEqual(record["model"], "gpt-x")
        lease = pe.load_document(self.task_dir / "lease.json")
        self.assertEqual(record["lease_generation"], lease["acquired_at"])
        self.assertEqual(records[0].stem, record["dispatch_id"])
        self.assertEqual(snapshot.stem, record["dispatch_id"])

    def test_the_attempt_event_carries_the_same_dispatch_id(self):
        code, _ = self.dispatch("docs/summary.md")
        self.assertEqual(code, 0)
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        events = [
            json.loads(line)
            for line in (self.task_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
        ]
        attempts = [e for e in events if e.get("type") == "worker_attempt"]
        self.assertEqual([e["dispatch_id"] for e in attempts], [record["dispatch_id"]])

    def test_publication_overwrites_an_existing_file(self):
        destination = self.repo / "docs" / "summary.md"
        destination.write_text("stale", encoding="utf-8")
        self.assertEqual(self.dispatch("docs/summary.md")[0], 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "published body")
        self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_failed_attempt_records_the_failure_and_writes_nothing_else(self):
        # Exit 0 with an empty `-o` file: the retry classifier calls this `ok`.
        code, runner = self.dispatch("docs/summary.md", attempt=codex_attempt(0, ""))
        self.assertEqual(code, 2)
        runner.assert_called_once()
        self.assertFalse((self.repo / "docs" / "summary.md").exists())
        self.assertEqual(list((self.task_dir / "outputs").glob("*.snapshot")), [])
        records = self.records()
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("snapshot", record)
        self.assertNotIn("sha256", record)

    def test_nonzero_exit_records_the_failure(self):
        code, _ = self.dispatch("docs/summary.md", attempt=codex_attempt(3, "half a document"))
        self.assertEqual(code, 2)
        self.assertFalse((self.repo / "docs" / "summary.md").exists())
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")

    def test_lease_reacquired_mid_run_publishes_nothing(self):
        def reacquire(*args, **kwargs):
            """Simulate the lease lapsing and being taken again by the same owner."""
            lease_path = self.task_dir / "lease.json"
            payload = pe.load_document(lease_path)
            payload["acquired_at"] = "2099-01-01T00:00:00Z"
            lease_path.write_text(json.dumps(payload), encoding="utf-8")
            return codex_attempt(0, "published body")

        code, _ = self.dispatch("docs/summary.md", run=reacquire)
        self.assertEqual(code, 2)
        self.assertFalse((self.repo / "docs" / "summary.md").exists())
        self.assertEqual(self.records(), [])
        self.assertFalse((self.task_dir / "outputs").exists())
        # The attempt event is a state write too, so it is refused by the same check
        # rather than appended to a task another conductor now owns.
        self.assertEqual(self.attempt_events(), [])

    def test_write_scope_narrowed_mid_run_refuses_the_publication(self):
        def narrow(*args, **kwargs):
            """A conductor that rewrote its own scope while the worker was running."""
            contract = pe.load_document(self.contract_path)
            contract["write_scope"] = ["nowhere"]
            self.contract_path.write_text(json.dumps(contract), encoding="utf-8")
            return codex_attempt(0, "published body")

        code, _ = self.dispatch("docs/summary.md", run=narrow)
        self.assertEqual(code, 2)
        self.assertFalse((self.repo / "docs" / "summary.md").exists())
        # The snapshot is written before the destination check, so it survives; the
        # record that would point at it does not.
        self.assertEqual(len(list((self.task_dir / "outputs").glob("*.snapshot"))), 1)
        self.assertEqual(self.records(), [])

    def test_code_suffix_is_refused_before_any_process_starts(self):
        for path in ("docs/tool.py", "docs/tool.SH", "docs/a/b/lib.ts"):
            with self.subTest(path):
                code, runner = self.dispatch(path)
                self.assertEqual(code, 2)
                runner.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_reserved_destination_is_refused_before_any_process_starts(self):
        code, runner = self.dispatch("tasks/t1/task.yaml")
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_out_of_scope_destination_is_refused_before_any_process_starts(self):
        self.write_contract(write_scope=("docs",))
        code, runner = self.dispatch("elsewhere/summary.md")
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_an_uninspectable_destination_is_unknown_not_success(self):
        # A clean worker exit plus an unreadable destination is not a success: the engine has
        # no verdict, and saying "succeeded" would invent one.
        with mock.patch.object(pe, "write_change_set",
                               return_value={"inspection_failed": "unreadable"}):
            code, _ = self.dispatch_write("docs/note.md")
        self.assertEqual(code, 2)
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "unknown")

    def test_a_mixed_authorship_artifact_blocks_the_reviewer(self):
        # Both families' output is in the artifact, so every candidate shares a family with
        # part of what it would review. Picking one anyway is the silent failure to avoid.
        pe.record_contributing_family(self.task_dir, "claude", "implementer")
        pe.record_contributing_family(self.task_dir, "codex", "implementer --write")
        code, runner = self.dispatch(None, role="critic")
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_an_unresolved_write_blocks_a_reviewer(self):
        # A killed dispatcher leaves changed files and never records who changed them, so the
        # sidecar still names whoever wrote last time.
        pe._state_path(self.task_dir, "outputs", "9" * 32, ".json").write_text(
            json.dumps({"dispatch_id": "9" * 32, "status": "in_flight", "mode": "write",
                        "destination": str(self.repo / "docs" / "note.md")}),
            encoding="utf-8",
        )
        code, runner = self.dispatch(None, role="critic")
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_write_and_out_together_are_refused(self):
        # Two authorities over one dispatch's result, with no rule for which wins.
        code, runner = self.dispatch_write("docs/note.md", out="docs/note.md")
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_an_implementer_on_codex_may_publish(self):
        # Same role, different host: Codex stays text mode, so there is no workspace to
        # conflict with and `--out` is available like it is to any other role.
        self.write_contract(role="implementer")
        code, runner = self.dispatch("docs/summary.md", role="implementer")
        self.assertEqual(code, 0)
        runner.assert_called_once()
        self.assertEqual(
            (self.repo / "docs" / "summary.md").read_text(encoding="utf-8"), "published body"
        )

    def dispatch_claude(self, out, stdout, exit_code=0, role="runner", min_bytes=0):
        """Dispatch a Claude worker with only the *process* mocked, so the real envelope
        parsing in ``_run_worker`` decides what was captured."""
        contract = pe.load_document(self.contract_path)
        backend = {"backend": "claude-fast-team", "host": "claude-code", "family": "claude",
                   "model": "claude-haiku-5", "effort": "low", "account": "team",
                   "config_dir": "/tmp/team"}
        spec = pe._claude_cli(backend)
        completed = subprocess.CompletedProcess(["claude"], exit_code, stdout=stdout, stderr="")
        with mock.patch.object(pe.subprocess, "run", return_value=completed), \
                mock.patch.object(pe, "build_worker_command", return_value=(["claude"], spec)), \
                mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", backend), "stubbed")), \
                mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            return pe.dispatch_worker(
                self.bundle, contract, role, self.brief, "native", False,
                None, self.task_dir, self.task_dir, out, self.contract_path, min_bytes,
            )

    def test_claude_publication_from_the_envelope(self):
        body = "# Findings\n\nThe answer is 42.\n"
        code = self.dispatch_claude(
            "docs/findings.md", json.dumps(envelope(result=body)) + "\n"
        )
        self.assertEqual(code, 0)
        destination = self.repo / "docs" / "findings.md"
        self.assertEqual(destination.read_text(encoding="utf-8"), body)
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "succeeded")
        self.assertIsNone(record["failure_reason"])
        self.assertEqual(record["family"], "claude")
        self.assertEqual(record["account"], "team")
        # The snapshot is byte-identical to what landed, which is what a later review binds.
        self.assertEqual(Path(record["snapshot"]).read_bytes(), destination.read_bytes())

    def test_malformed_stdout_records_malformed_envelope(self):
        for name, stdout in (
            ("no envelope at all", "I am thinking about it.\n"),
            ("non-JSON", "{not json}\n"),
            ("missing is_error", json.dumps({"type": "result", "subtype": "success",
                                             "result": "x"}) + "\n"),
            ("wrong subtype", json.dumps(envelope(subtype="error_max_turns")) + "\n"),
        ):
            with self.subTest(name):
                self.setUp()  # a clean repo, lease, and contract per case
                code = self.dispatch_claude("docs/findings.md", stdout)
                self.assertEqual(code, 2)
                self.assertFalse((self.repo / "docs" / "findings.md").exists())
                record = json.loads(self.records()[0].read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["failure_reason"], "malformed_envelope")

    def test_exit_zero_with_a_clean_envelope_but_nonzero_exit_is_named(self):
        code = self.dispatch_claude(
            "docs/findings.md", json.dumps(envelope()) + "\n", exit_code=1
        )
        self.assertEqual(code, 2)
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["failure_reason"], "nonzero_exit")

    def test_codex_failure_reasons_are_named(self):
        for result_text, reason in ((None, "missing_result_file"), ("", "empty_result_file")):
            with self.subTest(reason):
                self.setUp()
                code, _ = self.dispatch("docs/summary.md", attempt=codex_attempt(0, result_text))
                self.assertEqual(code, 2)
                record = json.loads(self.records()[0].read_text(encoding="utf-8"))
                self.assertEqual(record["failure_reason"], reason)

    def test_publication_comes_from_the_final_attempt_after_a_retry(self):
        # Team rate-limited, private retried: the record must describe the attempt that
        # actually produced the bytes, not the one that failed.
        self.write_contract(role="bulk_worker")
        contract = pe.load_document(self.contract_path)
        team = {"backend": "claude-fast-team", "host": "claude-code", "family": "claude",
                "model": "claude-haiku-5", "effort": "low", "account": "team",
                "config_dir": "/tmp/team"}
        limited = accounts.Attempt(
            "team", "/tmp/team", "claude-haiku-5", 1, "rate_limited",
            {"type": "result", "is_error": True, "api_error_status": 429, "result": "limit"},
            "", "",
        )
        final = claude_attempt(0, envelope(result="the second try"), "the second try")
        runner = mock.Mock(side_effect=[limited, final])
        seen = []

        def record_build(backend, *args, **kwargs):
            seen.append(backend["backend"])
            return ["claude"], pe._claude_cli(backend)

        with mock.patch.object(pe, "_run_worker", runner),                 mock.patch.object(pe, "build_worker_command", side_effect=record_build),                 mock.patch.object(pe, "_resolve_with_account",
                                  return_value=(pe.Decision(True, "resolved", team), "stubbed")),                 mock.patch.object(pe.accounts, "note_run_rate_limited"),                 mock.patch.object(pe.sys, "stdout"), mock.patch.object(pe.sys, "stderr"):
            code = pe.dispatch_worker(
                self.bundle, contract, "bulk_worker", self.brief, "native", False,
                None, self.task_dir, self.task_dir, "docs/retry.md", self.contract_path, 0,
            )
        self.assertEqual(code, 0)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(seen, ["claude-fast-team", "claude-fast"])
        self.assertEqual(
            (self.repo / "docs" / "retry.md").read_text(encoding="utf-8"), "the second try"
        )
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["attempt"], 2)
        self.assertEqual(record["backend"], "claude-fast")
        self.assertEqual(record["account"], "private")
        self.assertEqual(record["status"], "succeeded")
        # Both attempts are recorded under the one dispatch id.
        self.assertEqual(
            [(e["attempt"], e["dispatch_id"]) for e in self.attempt_events()],
            [(1, record["dispatch_id"]), (2, record["dispatch_id"])],
        )

    def test_dispatch_without_out_writes_no_records(self):
        code, runner = self.dispatch(None)
        self.assertEqual(code, 0)
        runner.assert_called_once()
        self.assertEqual(self.records(), [])
        self.assertFalse((self.repo / "docs" / "summary.md").exists())

    # The content gate between a clean worker exit and the destination write. A worker
    # that answers a document brief with a one-word refusal exits 0 with a well-formed
    # envelope, so no other check sees it -- and the destination may be a real note the
    # refusal would overwrite.
    def test_a_short_result_is_not_published(self):
        code, _ = self.dispatch(
            "docs/summary.md",
            attempt=codex_attempt(0, "CONTRACT_UNREADABLE"),
            min_bytes=pe.MIN_PUBLISH_BYTES,
        )
        self.assertEqual(code, 2)
        self.assertFalse((self.repo / "docs" / "summary.md").exists())
        record = json.loads(self.records()[0].read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure_reason"], "below_min_bytes")

    def test_whitespace_does_not_count_toward_the_floor(self):
        code, _ = self.dispatch(
            "docs/summary.md",
            attempt=codex_attempt(0, "short" + " " * 400),
            min_bytes=pe.MIN_PUBLISH_BYTES,
        )
        self.assertEqual(code, 2)
        self.assertFalse((self.repo / "docs" / "summary.md").exists())

    def test_a_long_enough_result_publishes(self):
        body = "# Summary\n\n" + ("a real paragraph of prose. " * 20)
        code, _ = self.dispatch(
            "docs/summary.md", attempt=codex_attempt(0, body), min_bytes=pe.MIN_PUBLISH_BYTES
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / "docs" / "summary.md").read_text(encoding="utf-8"), body)

    def test_zero_disables_the_gate(self):
        code, _ = self.dispatch("docs/summary.md", attempt=codex_attempt(0, "ok"), min_bytes=0)
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / "docs" / "summary.md").read_text(encoding="utf-8"), "ok")



class ObservedAuthorGuardTest(unittest.TestCase):
    """The authorship sidecar is a state write, and it decides reviewer independence."""

    def test_symlinked_sidecar_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            elsewhere = Path(tmp) / "elsewhere.json"
            elsewhere.write_text("{}", encoding="utf-8")
            (task_dir / pe.OBSERVED_AUTHOR_FILE).symlink_to(elsewhere)
            with self.assertRaises(ValueError):
                pe._state_file(task_dir, pe.OBSERVED_AUTHOR_FILE)
            self.assertEqual(elsewhere.read_text(encoding="utf-8"), "{}")

    def test_fixed_name_is_recognized_state(self):
        self.assertIn(pe.OBSERVED_AUTHOR_FILE, pe.RESERVED_TASK_FILES)
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            resolved = pe._state_file(task_dir, pe.OBSERVED_AUTHOR_FILE)
            self.assertEqual(resolved, task_dir / pe.OBSERVED_AUTHOR_FILE)


class WriteDispatchOutcomeTest(unittest.TestCase):
    """Outcomes and the prompt: both were reported fixed once while the edit never landed."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def test_the_prompt_names_the_single_destination(self):
        spec = pe._claude_cli({"backend": "claude-core", "host": "claude-code",
                               "family": "claude", "model": "m", "effort": "high"})
        completed = subprocess.CompletedProcess(["claude"], 0, stdout="{}", stderr="")
        task = {"task_id": "t"}
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("do the thing", encoding="utf-8")
            with mock.patch.object(pe.subprocess, "run", return_value=completed) as run:
                pe._run_worker(["claude"], spec, task, "implementer", brief, Path(tmp), None,
                               "/repo/docs/note.md")
            prompt = run.call_args.kwargs["input"]
            self.assertIn("You may write only inside /repo/docs/note.md", prompt)
            self.assertNotIn("Do not write to the filesystem", prompt)

            with mock.patch.object(pe.subprocess, "run", return_value=completed) as run:
                pe._run_worker(["claude"], spec, task, "critic", brief, Path(tmp), None, None)
            self.assertIn("Do not write to the filesystem", run.call_args.kwargs["input"])


class WriteTargetTest(unittest.TestCase):
    """Resolution of the one destination a `--write` worker may touch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "docs" / "note.md").write_text("before", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)
        self.task = {"target_repo": str(self.repo), "write_scope": ["docs/**"]}

    def resolve(self, path):
        return pe.resolve_write_target(str(self.repo), path, self.task)

    def test_an_existing_file_is_a_file_target(self):
        decision = self.resolve("docs/note.md")
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["kind"], "file")

    def test_an_existing_directory_is_a_directory_target(self):
        self.assertEqual(self.resolve("docs").details["kind"], "directory")

    def test_a_missing_path_is_a_file_whose_parent_must_exist(self):
        self.assertEqual(self.resolve("docs/new.md").details["kind"], "file")
        missing = self.resolve("docs/deeper/new.md")
        self.assertFalse(missing.allowed)
        self.assertIn("creates no directories", missing.reason)

    def test_refusals(self):
        cases = {
            "../escape.md": "may not contain",
            "docs/./note.md": "may not contain",
            "outside.md": "outside the contract's write_scope",
            ".git/config": "reserved",
        }
        for path, expected in cases.items():
            with self.subTest(path):
                decision = self.resolve(path)
                self.assertFalse(decision.allowed, path)
                self.assertIn(expected, decision.reason)

    def test_the_installation_is_refused_as_a_target(self):
        # The reserved-tree rules key on the real installation root, so this case cannot be
        # made from a fixture directory: it has to be the installation itself.
        task = {"target_repo": str(pe.INSTALLATION_ROOT), "write_scope": ["**"]}
        for path in ("engine/policy_engine.py", "policy/bindings.yaml", "_shared/learnings.md"):
            with self.subTest(path):
                decision = pe.resolve_write_target(str(pe.INSTALLATION_ROOT), path, task)
                self.assertFalse(decision.allowed, path)
                self.assertIn("reserved", decision.reason)

    def test_pattern_metacharacters_are_refused(self):
        # The destination becomes a permission pattern, so a literal `*` in a name would
        # authorize the siblings the glob reaches.
        for name in ("docs/no*e.md", "docs/n[o]te.md", "docs/note{1}.md", "docs/n!ote.md"):
            with self.subTest(name):
                decision = self.resolve(name)
                self.assertFalse(decision.allowed, name)
                self.assertIn("metacharacters", decision.reason)

    def test_a_directory_containing_reserved_state_is_refused(self):
        task_tree = pe.INSTALLATION_ROOT / "tasks"
        if not task_tree.is_dir():  # pragma: no cover - installation always has one
            self.skipTest("no installation task tree")
        task = {"target_repo": str(pe.INSTALLATION_ROOT), "write_scope": ["**"]}
        decision = pe.resolve_write_target(str(pe.INSTALLATION_ROOT), "tasks", task)
        self.assertFalse(decision.allowed)
        self.assertIn("reserved", decision.reason)

    def test_a_symlinked_target_is_refused(self):
        (self.repo / "docs" / "link.md").symlink_to(Path(self.tmp.name) / "elsewhere.md")
        self.assertFalse(self.resolve("docs/link.md").allowed)

    def test_a_symlinked_parent_is_refused(self):
        (Path(self.tmp.name) / "elsewhere").mkdir()
        (self.repo / "docs" / "sub").symlink_to(Path(self.tmp.name) / "elsewhere")
        decision = self.resolve("docs/sub/note.md")
        self.assertFalse(decision.allowed)
        self.assertIn("symlink", decision.reason)


class WritePermissionSettingsTest(unittest.TestCase):
    """The generated permission document is the whole boundary, so its shape is load-bearing."""

    def test_a_file_target_authorizes_exactly_one_path(self):
        document = pe.write_permission_settings("/repo/docs/note.md", "file")
        self.assertEqual(document["permissions"]["allow"], ["Edit(///repo/docs/note.md)"])
        self.assertEqual(document["permissions"]["deny"], [])

    def test_only_edit_rules_are_emitted(self):
        # The CLI matches file permission checks against `Edit` alone; a `Write(...)` rule
        # authorizes nothing (measured). Emitting one would look like a guard and be inert.
        for kind in ("file", "directory"):
            document = pe.write_permission_settings("/repo/docs", kind)
            rules = document["permissions"]["allow"] + document["permissions"]["deny"]
            with self.subTest(kind):
                self.assertTrue(rules)
                self.assertTrue(all(rule.startswith("Edit(") for rule in rules), rules)

    def test_every_pattern_uses_the_absolute_double_slash_form(self):
        # A single leading slash is not the absolute spelling: such a rule matches nothing and
        # fails open, which is how a generated allowlist would silently authorize everything.
        document = pe.write_permission_settings("/repo/docs", "directory")
        for rule in document["permissions"]["allow"] + document["permissions"]["deny"]:
            with self.subTest(rule):
                # `//` plus an absolute path, so a rule reads `Write(///repo/...)`. A single
                # leading slash is a different, silently non-matching form.
                self.assertRegex(rule, r"^Edit\(///")

    def test_a_directory_target_denies_reserved_paths_beneath_it(self):
        # Asserted by matching representative paths rather than by pinning literal pattern
        # strings: the patterns' spelling is an implementation detail, their coverage is not.
        # The live proof that the CLI honors them is the `docs/.git/config` refusal.
        deny = pe.write_permission_settings("/repo/docs", "directory")["permissions"]["deny"]
        patterns = [rule[len("Edit(//"):-1] for rule in deny]
        covered = (
            "/repo/docs/.git/config", "/repo/docs/.github/workflows/ci.yml",
            "/repo/docs/.gitignore", "/repo/docs/.gitmodules",
            "/repo/docs/.claude/settings.json", "/repo/docs/.codex/config.toml",
            "/repo/docs/.mcp.json", "/repo/docs/sub/task.yaml", "/repo/docs/sub/lease.json",
            "/repo/docs/sub/outputs/x.json", "/repo/docs/sub/writes/x.before",
            "/repo/docs/tasks/.active-task",
        )
        for path in covered:
            with self.subTest(path):
                self.assertTrue(
                    any(fnmatch.fnmatch(path, pattern) for pattern in patterns), path
                )

    def test_the_deny_patterns_do_not_cover_ordinary_content(self):
        deny = pe.write_permission_settings("/repo/docs", "directory")["permissions"]["deny"]
        patterns = [rule[len("Edit(//"):-1] for rule in deny]
        for path in ("/repo/docs/note.md", "/repo/docs/sub/figure.png", "/repo/docs/digital.md"):
            with self.subTest(path):
                self.assertFalse(
                    any(fnmatch.fnmatch(path, pattern) for pattern in patterns), path
                )


class WorkerProfileTest(unittest.TestCase):
    """What a dispatched Claude worker inherits, and what it is told instead."""

    def spec(self):
        return pe._claude_cli({"backend": "claude-core", "host": "claude-code",
                               "family": "claude", "model": "m", "effort": "high"})

    def test_every_claude_worker_is_restricted(self):
        # Without this the worker picks up the operator's plugins, hooks and permission
        # entries -- which is how a coding persona was reaching literature-note work.
        self.assertIn("--restricted", self.spec().args)

    def test_the_baseline_replaces_what_restricted_removes(self):
        args = self.spec().args
        self.assertIn("--append-system-prompt", args)
        prompt = args[args.index("--append-system-prompt") + 1]
        self.assertIn("American spelling", prompt)
        self.assertIn("one-shot worker", prompt)
        # The research-code rules govern a returned patch as much as a direct write, so
        # dropping them with the rest of the inherited profile would have been a real loss.
        self.assertIn("generated outputs never do", prompt)
        self.assertIn("docstrings", prompt)

    def test_the_write_branch_does_not_double_the_flag(self):
        backend = {"backend": "claude-core", "host": "claude-code", "family": "claude",
                   "model": "m", "effort": "high"}
        _, spec = pe.build_worker_command(
            backend, "native", Path("/repo"), "implementer",
            write_settings=Path("/tmp/generated.json"),
        )
        self.assertEqual(spec.args.count("--restricted"), 1)
        self.assertIn("Read,Grep,Glob,Write,Edit,NotebookEdit", spec.args)


class ContributingFamiliesTest(unittest.TestCase):
    """Authorship accumulates, and a mixed artifact has no independent reviewer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_second_family_is_added_not_substituted(self):
        pe.record_contributing_family(self.dir, "codex", "implementer via dispatch-worker")
        pe.record_contributing_family(self.dir, "claude", "implementer via dispatch-worker --write")
        self.assertEqual(pe.observed_author_families(self.dir), ["claude", "codex"])
        record = json.loads((self.dir / pe.OBSERVED_AUTHOR_FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["family"], "claude")  # latest, for older readers
        self.assertEqual(record["families"], ["claude", "codex"])

    def test_the_same_family_twice_stays_one_entry(self):
        pe.record_contributing_family(self.dir, "claude", "a")
        pe.record_contributing_family(self.dir, "claude", "b")
        self.assertEqual(pe.observed_author_families(self.dir), ["claude"])

    def test_an_older_single_family_record_still_reads(self):
        (self.dir / pe.OBSERVED_AUTHOR_FILE).write_text(
            json.dumps({"family": "codex", "source": "implementer"}), encoding="utf-8"
        )
        self.assertEqual(pe.observed_author_families(self.dir), ["codex"])

    def test_absence_is_empty_and_damage_raises(self):
        self.assertEqual(pe.observed_author_families(self.dir), [])
        (self.dir / pe.OBSERVED_AUTHOR_FILE).write_text("{ not json", encoding="utf-8")
        with self.assertRaises(pe.UnreadableAuthorRecord):
            pe.observed_author_families(self.dir)

    def test_damage_keeps_the_families_that_were_still_legible(self):
        (self.dir / pe.OBSERVED_AUTHOR_FILE).write_text(
            json.dumps({"families": ["codex"], "damaged": "earlier corruption"}),
            encoding="utf-8",
        )
        pe.record_contributing_family(self.dir, "claude", "implementer --write")
        record = json.loads((self.dir / pe.OBSERVED_AUTHOR_FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["families"], ["claude", "codex"])
        self.assertIn("damaged", record)
        with self.assertRaises(pe.UnreadableAuthorRecord):
            pe.observed_author_families(self.dir)

    def test_an_unknown_family_in_the_list_raises(self):
        (self.dir / pe.OBSERVED_AUTHOR_FILE).write_text(
            json.dumps({"families": ["claude", "acme"]}), encoding="utf-8"
        )
        with self.assertRaises(pe.UnreadableAuthorRecord):
            pe.observed_author_families(self.dir)


class OverlapAndReservationTest(unittest.TestCase):
    """Two holes round 6 found by looking wider than the last fix."""

    def test_overlapping_destinations_count_as_the_same_place(self):
        # A live worker writing `docs/note.md` does not equal `docs`, but restoring the
        # directory would take its file with it.
        self.assertTrue(pe._destinations_overlap("/repo/docs", "/repo/docs/note.md"))
        self.assertTrue(pe._destinations_overlap("/repo/docs/note.md", "/repo/docs"))
        self.assertTrue(pe._destinations_overlap("/repo/docs", "/repo/docs"))
        self.assertFalse(pe._destinations_overlap("/repo/docs", "/repo/src"))
        self.assertFalse(pe._destinations_overlap("/repo/docs", None))

    def test_an_unresolved_reservation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self.assertEqual(pe._unresolved_write_reservations(task_dir), [])
            pe._state_path(task_dir, "outputs", "a" * 32, ".json").write_text(
                json.dumps({"dispatch_id": "a" * 32, "status": "in_flight"}), encoding="utf-8"
            )
            pe._state_path(task_dir, "outputs", "b" * 32, ".json").write_text(
                json.dumps({"dispatch_id": "b" * 32, "status": "succeeded"}), encoding="utf-8"
            )
            self.assertEqual(pe._unresolved_write_reservations(task_dir), ["a" * 32])

    def test_an_unreadable_record_counts_as_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            pe._state_path(task_dir, "outputs", "c" * 32, ".json").write_text(
                "{ truncated", encoding="utf-8"
            )
            self.assertEqual(pe._unresolved_write_reservations(task_dir), ["c" * 32])


class RestoreSafetyTest(unittest.TestCase):
    """Restore's failure paths, which are the ones that can destroy data."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.dispatch = "c" * 32
        self.addCleanup(self.tmp.cleanup)

    def record(self, destination, baseline, status="succeeded", after=None):
        pe._state_path(self.task_dir, "outputs", self.dispatch, ".json").write_text(
            json.dumps({"dispatch_id": self.dispatch, "status": status, "mode": "write",
                        "destination": str(destination), "baseline": baseline,
                        "after": after if after is not None else {}}),
            encoding="utf-8",
        )

    def test_an_unowned_sibling_is_not_deleted(self):
        target = self.root / "note.md"
        target.write_text("before", encoding="utf-8")
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, target, "file")
        target.write_text("after", encoding="utf-8")
        self.record(target, captured.details,
                    after={"": {"sha256": pe.hashlib.sha256(b"after").hexdigest(), "bytes": 5}})
        # A file sitting where a predictable staging path would go must survive.
        decoy = self.root / "note.md.restoring"
        decoy.write_text("somebody else's file", encoding="utf-8")
        decision = pe.restore_write(self.task_dir, self.dispatch)
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")
        self.assertEqual(decoy.read_text(encoding="utf-8"), "somebody else's file")

    def test_a_live_dispatch_blocks_restore(self):
        target = self.root / "live.md"
        target.write_text("before", encoding="utf-8")
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, target, "file")
        self.record(target, captured.details)
        pe._state_path(self.task_dir, "outputs", "d" * 32, ".json").write_text(
            json.dumps({"dispatch_id": "d" * 32, "status": "in_flight",
                        "destination": str(target)}),
            encoding="utf-8",
        )
        decision = pe.restore_write(self.task_dir, self.dispatch)
        self.assertFalse(decision.allowed)
        self.assertIn("writing", decision.reason)

    def test_a_target_that_did_not_exist_is_removed(self):
        target = self.root / "created.md"
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, target, "file")
        target.write_text("the worker made this", encoding="utf-8")
        self.record(target, captured.details,
                    after={"": {"sha256": pe.hashlib.sha256(b"the worker made this").hexdigest(),
                                "bytes": 20}})
        decision = pe.restore_write(self.task_dir, self.dispatch)
        self.assertTrue(decision.allowed, decision.reason)
        self.assertFalse(target.exists())


class InterruptedWriteTest(unittest.TestCase):
    """A dispatcher killed mid-write must leave a state someone can get out of."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.dispatch = "e" * 32
        self.target = self.root / "note.md"
        self.target.write_text("before", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, self.target, "file")
        pe._state_path(self.task_dir, "outputs", self.dispatch, ".json").write_text(
            json.dumps({"dispatch_id": self.dispatch, "status": "in_flight", "mode": "write",
                        "destination": str(self.target), "baseline": captured.details}),
            encoding="utf-8",
        )
        self.target.write_text("half-written by a worker that died", encoding="utf-8")

    def test_it_refuses_without_an_explicit_assertion(self):
        decision = pe.restore_write(self.task_dir, self.dispatch)
        self.assertFalse(decision.allowed)
        self.assertIn("--assume-stopped", decision.reason)
        self.assertEqual(self.target.read_text(encoding="utf-8"),
                         "half-written by a worker that died")

    def test_assume_stopped_restores_from_the_baseline(self):
        decision = pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True)
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before")

    def test_a_successful_recovery_retires_its_reservation(self):
        # Otherwise the recovered record blocks every later restore of this destination, and
        # this forced restore could be repeated over whatever was written since.
        self.assertTrue(pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True).allowed)
        record = json.loads(
            pe._state_path(self.task_dir, "outputs", self.dispatch, ".json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(record["status"], "recovered")
        self.assertTrue(record["after"])

        # A later dispatch against the SAME destination must not be blocked by the retired
        # record -- a different destination would not exercise the blocking this test exists
        # to prove.
        later = "1" * 32
        captured = pe.capture_write_baseline(self.task_dir, later, self.target, "file")
        self.target.write_text("the later dispatch wrote this", encoding="utf-8")
        pe._state_path(self.task_dir, "outputs", later, ".json").write_text(
            json.dumps({"dispatch_id": later, "status": "succeeded", "mode": "write",
                        "destination": str(self.target), "baseline": captured.details,
                        "after": {"": {"sha256": pe.hashlib.sha256(
                            b"the later dispatch wrote this").hexdigest(), "bytes": 29}}}),
            encoding="utf-8",
        )
        self.assertTrue(pe.restore_write(self.task_dir, later).allowed)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before")

    def test_an_uninspectable_destination_after_recovery_blocks_the_next_restore(self):
        # The restore path inspects the destination twice -- once to compare against the
        # recorded after-state, once after replacing it -- and the baseline in between. Only
        # the second inspection *of the destination* is the post-recovery one.
        real = pe._write_manifest
        seen = {"destination": 0}

        def flaky(path, kind):
            if path == self.target:
                seen["destination"] += 1
                if seen["destination"] == 2:
                    return pe.Decision(False, "transient read failure")
            return real(path, kind)

        with mock.patch.object(pe, "_write_manifest", flaky):
            self.assertTrue(
                pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True).allowed
            )
        record = json.loads(
            pe._state_path(self.task_dir, "outputs", self.dispatch, ".json")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("after", record)
        self.assertIn("transient read failure", record["after_unknown"])
        again = pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True)
        self.assertFalse(again.allowed)
        self.assertIn("could not be inspected", again.reason)
        # The wording must not suggest that looking at the file now restores the option:
        # the missing observation cannot be reconstructed after the fact.
        self.assertIn("not available for this dispatch again", again.reason)
        self.assertIn(".before", again.reason)  # names the baseline for a manual put-back

    def test_repeating_a_recovery_after_later_edits_is_refused(self):
        self.assertTrue(pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True).allowed)
        self.target.write_text("somebody's later work", encoding="utf-8")
        again = pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True)
        self.assertFalse(again.allowed)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "somebody's later work")

    def test_an_unreadable_neighbouring_record_stops_the_restore(self):
        # It may be a half-written reservation for this very destination; stepping past it is
        # how a live worker's changes disappear.
        pe._state_path(self.task_dir, "outputs", "f" * 32, ".json").write_text(
            "{ truncated", encoding="utf-8"
        )
        decision = pe.restore_write(self.task_dir, self.dispatch, assume_stopped=True)
        self.assertFalse(decision.allowed)
        self.assertIn("cannot be read", decision.reason)


class ReadScopeTest(unittest.TestCase):
    """Additional read roots: authorization, not containment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sources").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_an_existing_directory_is_authorized_and_canonical(self):
        decision = pe.resolve_read_scope([str(self.root / "sources")])
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["roots"], [str((self.root / "sources").resolve())])

    def test_a_trailing_glob_is_accepted(self):
        decision = pe.resolve_read_scope([str(self.root / "sources") + "/**"])
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["roots"], [str((self.root / "sources").resolve())])

    def test_absent_scope_is_empty_not_an_error(self):
        self.assertEqual(pe.resolve_read_scope(None).details["roots"], [])

    def test_refusals(self):
        cases = {
            "sources": "must be absolute",
            str(self.root / "missing"): "not an existing directory",
            str(self.root / "sources") + "/../etc": "may not contain",
            str(Path.home()): "too broad",
            # `/` is caught by the empty-segment rule before the breadth rule reaches it.
            # Refused either way; this pins which message actually comes back.
            "/": "may not contain",
            str(Path.home() / ".ssh"): "credentials or agent configuration",
            str(Path.home() / ".claude" / "multiagent"): "credentials or agent configuration",
            str(Path.home()) + "/.codex/skills": "credentials or agent configuration",
        }
        for entry, expected in cases.items():
            with self.subTest(entry):
                decision = pe.resolve_read_scope([entry])
                self.assertFalse(decision.allowed, entry)
                self.assertIn(expected, decision.reason)

    def test_a_non_list_is_refused(self):
        self.assertFalse(pe.resolve_read_scope("sources").allowed)

    def test_roots_become_add_dir_arguments_for_claude_only(self):
        backend = {"backend": "claude-core", "host": "claude-code", "family": "claude",
                   "model": "m", "effort": "high"}
        command, spec = pe.build_worker_command(
            backend, "native", Path("/repo"), "implementer", read_roots=["/srv/papers"]
        )
        self.assertEqual(spec.args[:2], ["--add-dir", "/srv/papers"])
        # A Codex reviewer of a contract that declares a read scope must stay dispatchable:
        # its sandbox already reaches those paths, so there is nothing to add and nothing to
        # refuse. Raising here would break the ordinary Claude-author, Codex-review sequence.
        codex = {"backend": "codex-core", "host": "codex", "family": "codex",
                 "model": "m", "effort": "high"}
        _, codex_spec = pe.build_worker_command(
            codex, "native", Path("/repo"), "implementer", read_roots=["/srv/papers"]
        )
        self.assertNotIn("--add-dir", codex_spec.args)
        self.assertIn("read_scope", codex_spec.enforcement)


class ManagedSettingsGateTest(unittest.TestCase):
    """`--write` trusts its generated allowlist only while nothing else can add to it."""

    def test_no_managed_settings_on_this_host(self):
        # Records the premise rather than asserting a wish: if one appears, this fails and the
        # gate below is what stops `--write` from claiming a boundary it no longer has.
        self.assertIsNone(pe.managed_settings_present())

    def test_a_managed_file_refuses_the_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            managed = Path(tmp) / "managed-settings.json"
            managed.write_text("{}", encoding="utf-8")
            with mock.patch.object(pe, "MANAGED_SETTINGS_PATHS", (managed,)):
                self.assertEqual(pe.managed_settings_present(), managed)


class RestoreLockTest(unittest.TestCase):
    """Restore compares, deletes and replaces; all three belong inside one lock."""

    def test_restore_holds_the_task_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            held = []
            real_lock = pe._lease_lock

            def watched(directory, timeout=10.0):
                held.append(directory)
                return real_lock(directory, timeout)

            with mock.patch.object(pe, "_lease_lock", watched):
                decision = pe.restore_write(task_dir, "b" * 32)
            self.assertEqual(held, [task_dir])
            self.assertFalse(decision.allowed)  # no record for this dispatch
            self.assertIn("no dispatch record", decision.reason)


class WriteBaselineTest(unittest.TestCase):
    """The baseline is what makes a direct write reversible."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.addCleanup(self.tmp.cleanup)
        self.dispatch = "a" * 32

    def test_a_file_baseline_round_trips(self):
        target = self.root / "note.md"
        target.write_text("before", encoding="utf-8")
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, target, "file")
        self.assertTrue(captured.allowed, captured.reason)
        target.write_text("after", encoding="utf-8")
        changes = pe.write_change_set(captured.details, target, "file")
        self.assertEqual(changes["modified"], [""])
        self.assertTrue(pe.changed_anything(changes))

    def test_an_absent_target_is_recorded_as_absent(self):
        target = self.root / "new.md"
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, target, "file")
        self.assertTrue(captured.allowed)
        self.assertFalse(captured.details["existed"])
        target.write_text("made", encoding="utf-8")
        self.assertEqual(pe.write_change_set(captured.details, target, "file")["created"], [""])

    def test_a_symlink_in_a_directory_target_refuses_the_baseline(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "real.md").write_text("x", encoding="utf-8")
        (tree / "link.md").symlink_to(self.root / "elsewhere.md")
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, tree, "directory")
        self.assertFalse(captured.allowed)
        self.assertIn("symlink", captured.reason)

    def test_the_baseline_is_bounded(self):
        tree = self.root / "big"
        tree.mkdir()
        for index in range(pe.WRITE_BASELINE_MAX_FILES + 2):
            (tree / f"{index}.txt").write_text("x", encoding="utf-8")
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, tree, "directory")
        self.assertFalse(captured.allowed)
        self.assertIn("baseline limit", captured.reason)

    def test_an_unreadable_change_set_is_not_an_empty_one(self):
        # "nothing changed" and "nobody could tell" must never look alike.
        tree = self.root / "tree2"
        tree.mkdir()
        captured = pe.capture_write_baseline(self.task_dir, self.dispatch, tree, "directory")
        (tree / "link.md").symlink_to(self.root / "elsewhere.md")
        changes = pe.write_change_set(captured.details, tree, "directory")
        self.assertIn("inspection_failed", changes)
        self.assertTrue(pe.changed_anything(changes))



if __name__ == "__main__":
    unittest.main()
