"""Tests for the Codex PreToolUse adapter: scope, translation, and the shared decisions."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

import policy_engine as pe  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("codex_pretool", ENGINE / "adapters" / "codex_pretool.py")
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)


class CodexAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = pe.load_policy(ROOT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "multiagent"
        self.task_dir = self.root / "tasks" / "t1"
        self.task_dir.mkdir(parents=True)
        (self.root / "docs").mkdir()
        self.task_path = self.task_dir / "task.yaml"
        self.write_contract(("implementer", "critic"), role="implementer", author="codex")

    def write_contract(self, roles, role, author):
        self.task_path.write_text(json.dumps({
            "schema_version": 1, "task_id": "t1", "status": "active", "target_repo": str(self.root),
            "write_scope": ["docs/**"], "roles_plan": list(roles), "author_family": author,
            "audit_cycles": 1, "approvals": {"user": []}, "dispatch": {"current_role": role},
            "conductor": {"host": "codex", "backend": "codex-frontier", "lease_owner": "me"},
        }), encoding="utf-8")

    def run_event(self, event, active=True, raw=None):
        """Drive the adapter's main() with one event; return the denial reason or None."""
        out = io.StringIO()
        hook = adapter.load_hook()
        with mock.patch.object(adapter, "ROOT", self.root), \
                mock.patch.object(hook, "active_task_path",
                                  return_value=self.task_path if active else None), \
                mock.patch.object(hook, "load_policy", return_value=self.bundle), \
                mock.patch.object(adapter.sys, "stdin", io.StringIO(raw if raw is not None else json.dumps(event))), \
                mock.patch.object(hook.sys, "stdout", out):
            self.assertEqual(adapter.main(), 0)
        text = out.getvalue().strip()
        if not text:
            return None
        payload = json.loads(text)["hookSpecificOutput"]
        self.assertEqual((payload["hookEventName"], payload["permissionDecision"]), ("PreToolUse", "deny"))
        return payload["permissionDecisionReason"]

    def shell(self, command, cwd=None):
        return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.root)}

    def patch(self, body, cwd=None):
        return {"tool_name": "apply_patch", "tool_input": {"command": body}, "cwd": str(cwd or self.root)}

    def test_sessions_outside_the_installation_or_without_a_task_are_untouched(self):
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        self.assertIsNone(self.run_event(self.shell("echo x > f", cwd=outside)))
        self.assertIsNone(self.run_event(self.shell("echo x > f"), active=False))
        self.assertIsNone(self.run_event(self.patch("garbage"), active=False))

    def test_shell_rules_match_the_claude_hook(self):
        self.assertIn("shell-based mutation", self.run_event(self.shell("echo x > f")))
        self.assertIn("destructive_action", self.run_event(self.shell("rm -rf docs")))
        self.assertIn("worker CLI", self.run_event(self.shell("claude -p hi")))
        self.assertIsNone(self.run_event(self.shell("ls docs")))
        self.assertIsNone(self.run_event({**self.shell(""), "tool_input": {"command": ["ls", "docs"]}}))

    def test_apply_patch_checks_every_file_against_the_write_scope(self):
        inside = "*** Begin Patch\n*** Add File: docs/new.md\n+hello\n*** End Patch\n"
        self.assertIsNone(self.run_event(self.patch(inside)))
        outside = "*** Begin Patch\n*** Update File: engine/policy_engine.py\n@@\n-a\n+b\n*** End Patch\n"
        self.assertIn("outside target_repo/write_scope", self.run_event(self.patch(outside)))
        moved = ("*** Begin Patch\n*** Update File: docs/a.md\n*** Move to: README.md\n@@\n-a\n+b\n"
                 "*** End Patch\n")
        self.assertIn("outside target_repo/write_scope", self.run_event(self.patch(moved)))
        absolute = f"*** Begin Patch\n*** Delete File: {self.root}/docs/old.md\n*** End Patch\n"
        self.assertIsNone(self.run_event(self.patch(absolute)))

    def test_an_unreadable_patch_is_refused_while_a_task_is_active(self):
        self.assertIn("failed closed", self.run_event(self.patch("no headers here")))

    def test_a_codex_child_is_codex_for_the_independence_check(self):
        pe.record_contributing_family(self.task_dir, "codex", "implementer via spawn_agent")
        self.write_contract(("implementer", "critic"), role="critic", author="codex")
        reason = self.run_event({"tool_name": "collaborationspawn_agent", "tool_input": {},
                                 "cwd": str(self.root)})
        self.assertIn("codex cannot review an artifact codex produced", reason)

    def test_a_codex_producer_spawn_is_recorded_as_codex(self):
        self.assertIsNone(self.run_event({"tool_name": "collaborationspawn_agent", "tool_input": {},
                                          "cwd": str(self.root)}))
        self.assertEqual(pe.observed_author_families(self.task_dir), ["codex"])

    def test_the_adapters_own_errors_deny(self):
        with mock.patch.object(adapter, "translate", side_effect=RuntimeError("boom")):
            self.assertIn("failed closed", self.run_event(self.shell("ls")))

    def test_relative_patch_paths_resolve_against_a_subfolder_cwd(self):
        sub = self.root / "docs"
        self.assertIsNone(self.run_event(self.patch("*** Begin Patch\n*** Add File: new.md\n+x\n*** End Patch\n", cwd=sub)))
        escaped = "*** Begin Patch\n*** Add File: ../engine/x.py\n+x\n*** End Patch\n"
        self.assertIn("outside target_repo/write_scope", self.run_event(self.patch(escaped, cwd=sub)))

    def test_a_sub_agent_edit_is_checked_as_its_role(self):
        # agent_id marks a sub-agent's call; its actor is the contract's current role.
        self.write_contract(("implementer", "critic"), role="critic", author="codex")
        event = {**self.patch("*** Begin Patch\n*** Add File: docs/new.md\n+x\n*** End Patch\n"),
                 "agent_id": "child-1"}
        self.assertIn("may not write", self.run_event(event))

    def test_list_form_and_cmd_shell_commands_are_checked(self):
        listed = {**self.shell(""), "tool_input": {"command": ["rm", "-rf", "docs"]}}
        self.assertIn("destructive_action", self.run_event(listed))
        cmd_form = {"tool_name": "exec_command", "tool_input": {"cmd": "echo x > f"}, "cwd": str(self.root)}
        self.assertIn("shell-based mutation", self.run_event(cmd_form))

    def test_unreadable_input_is_never_refused(self):
        self.assertIsNone(self.run_event(None, raw="{ not json"))

    def test_any_spawn_agent_namespace_is_a_spawn(self):
        pe.record_contributing_family(self.task_dir, "codex", "implementer via spawn_agent")
        self.write_contract(("implementer", "critic"), role="critic", author="codex")
        reason = self.run_event({"tool_name": "agents.spawn_agent", "tool_input": {}, "cwd": str(self.root)})
        self.assertIn("codex cannot review", reason)

    def test_other_collaboration_tools_pass(self):
        self.assertIsNone(self.run_event({"tool_name": "collaborationwait_agent", "tool_input": {},
                                          "cwd": str(self.root)}))


if __name__ == "__main__":
    unittest.main()
