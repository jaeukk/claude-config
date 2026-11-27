"""Regression tests for a recoverable global settings migration and scoped skill inputs."""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name+".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("global-instructions")
skills = load("shared-skill")


class GlobalSettingsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.rules = self.home/".claude/multiagent/global-instructions.md"
        self.rules.parent.mkdir(parents=True)
        self.rules.write_text("revision A\n")
        self.codex = self.home/".codex/AGENTS.md"
        self.codex.parent.mkdir()
        self.gemini = self.home/".gemini/GEMINI.md"
        self.gemini.parent.mkdir()
        self.gemini.write_text("a custom supplement")

    def test_install_update_and_rollback(self):
        self.codex.write_text("legacy rule"); self.codex.chmod(0o640)
        before = installer.snapshot(self.codex)
        with patch.object(installer,"LEGACY_CODEX",installer.hashlib.sha256(self.codex.read_bytes()).hexdigest()):
            installer.manage(self.home,"apply")
            installer.manage(self.home,"apply")
            for entry in installer.ENTRIES[:3]:
                self.assertTrue((self.home/entry).samefile(self.rules))
            self.rules.write_text("revision B\n")
            self.assertEqual(self.codex.read_text(),"revision B\n")
            installer.manage(self.home,"rollback")
        self.assertEqual(installer.snapshot(self.codex),before)
        self.assertEqual(self.gemini.read_text(),"a custom supplement")
        self.assertFalse((self.home/".agents/AGENTS.md").is_symlink())

    def test_unknown_collision_preflight(self):
        self.codex.write_text("another rule")
        with self.assertRaises(ValueError):
            installer.manage(self.home,"apply")
        self.assertFalse((self.home/".agents/AGENTS.md").is_symlink())

    def test_interrupted_install_and_external_edit(self):
        original = installer.replace
        def interrupt(path, state):
            if path == self.codex: raise InterruptedError()
            original(path,state)
        with patch.object(installer,"replace",interrupt), self.assertRaises(InterruptedError):
            installer.manage(self.home,"apply")
        installer.manage(self.home,"apply")
        self.codex.unlink()
        self.codex.write_text("user edit")
        with self.assertRaises(ValueError): installer.manage(self.home,"rollback")
        self.assertEqual(self.codex.read_text(),"user edit")
        self.assertTrue((self.home/".gemini/AGENTS.md").samefile(self.rules))

    def test_override_and_missing_source_on_resume(self):
        override = self.codex.with_name("AGENTS.override.md")
        override.write_text("shadow")
        with self.assertRaises(ValueError):
            installer.manage(self.home, "apply")
        override.unlink()
        installer.manage(self.home, "apply")
        self.rules.unlink()
        with self.assertRaises(ValueError):
            installer.manage(self.home, "apply")
        installer.manage(self.home, "rollback")
        self.assertFalse(self.codex.is_symlink())

    def test_profile_reset_and_new_supplement(self):
        self.codex.write_text("legacy")
        legacy = installer.hashlib.sha256(self.codex.read_bytes()).hexdigest()
        persona = installer.hashlib.sha256(self.gemini.read_bytes()).hexdigest()
        with patch.object(installer,"LEGACY_CODEX",legacy), patch.object(installer,"LEGACY_GEMINI",persona):
            installer.manage(self.home,"apply")
            self.codex.unlink()
            self.gemini.write_text("new personal supplement")
            installer.manage(self.home,"apply")
            self.assertTrue(self.codex.samefile(self.rules))
            self.assertEqual(self.gemini.read_text(),"new personal supplement")
            installer.manage(self.home,"rollback")
            self.assertEqual(self.codex.read_text(),"legacy")
            self.assertEqual(self.gemini.read_text(),"new personal supplement")

    def test_repair_preexisting_link(self):
        self.codex.symlink_to(self.rules)
        installer.manage(self.home,"apply")
        self.codex.unlink()
        installer.manage(self.home,"apply")
        self.assertTrue(self.codex.samefile(self.rules))

    def test_removed_persona_restores_exactly(self):
        before = installer.snapshot(self.gemini)
        digest = installer.hashlib.sha256(self.gemini.read_bytes()).hexdigest()
        with patch.object(installer, "LEGACY_GEMINI", digest):
            installer.manage(self.home, "apply")
            self.assertFalse(self.gemini.exists())
            installer.manage(self.home, "rollback")
        self.assertEqual(installer.snapshot(self.gemini), before)

    def test_invalid_global_source_preflight(self):
        for content in (b"", b"x"*8193, bytes([255])):
            with self.subTest(length=len(content)):
                self.rules.write_bytes(content)
                with self.assertRaises(ValueError):
                    installer.manage(self.home,"apply")
                self.assertFalse(self.codex.is_symlink())

    def test_interrupted_worker_releases_scope(self):
        # A real subprocess runs the wrapper and a local stand-in for the dispatcher.
        skill = self.home/".claude/skills/demo"
        skill.mkdir(parents=True)
        (skill/"SKILL.md").write_text("synthetic definition")
        wrapper = self.home/"scripts/shared-skill.py"
        wrapper.parent.mkdir()
        wrapper.write_bytes(Path(skills.__file__).read_bytes())
        engine = self.home/"multiagent/engine/policy_engine.py"
        engine.parent.mkdir(parents=True)
        engine.write_text(
            "from pathlib import Path\nimport os, sys, time\n"
            "scope = sys.argv[sys.argv.index('--read-scope')+1]\n"
            "out = Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.write_text(str(os.getpid())+'\\n'+scope)\ntime.sleep(60)\n"
        )
        brief = self.home/"brief"
        brief.write_text("test")
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                out = self.home/f"started-{sig}"
                bootstrap = ("from pathlib import Path; from unittest.mock import patch; import runpy;"
                             f"patch.object(Path,'home',return_value=Path({str(self.home)!r})).start();"
                             f"runpy.run_path({str(wrapper)!r},run_name='__main__')")
                args = [sys.executable,"-c",bootstrap,"produce","--skill","demo","--brief",str(brief),
                        "--target-repo",str(self.home),"--out",str(out),"--conductor-host","codex"]
                proc = subprocess.Popen(args,stderr=subprocess.DEVNULL)
                try:
                    until = time.monotonic()+5
                    while not out.exists() and time.monotonic()<until and proc.poll() is None:
                        time.sleep(0.02)
                    pid, scope = out.read_text().splitlines()
                    proc.send_signal(sig)
                    self.assertNotEqual(proc.wait(timeout=8),0)
                    self.assertFalse(Path(scope).exists())
                    with self.assertRaises(ProcessLookupError): os.kill(int(pid),0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()

    def test_skill_scope_and_permissions(self):
        directory = self.home/".claude/skills/demo"
        directory.mkdir(parents=True)
        (directory/"SKILL.md").write_text("body")
        (directory/"ref").write_text("reference")
        (directory/"outside").symlink_to(self.rules)
        for invalid in ("outside","../global-instructions.md","/etc/passwd","*"):
            with self.assertRaises((ValueError,FileNotFoundError)):
                skills.selected_files(self.home,"demo",[invalid])
        files = skills.selected_files(self.home,"demo",["ref"])
        config = self.home/".gemini/config"
        config.mkdir()
        (config/"skills.json").write_text(json.dumps({"entries":[{"path":str(directory.parent),"include_only":["demo"]}]}))
        settings = self.home/".gemini/antigravity-cli/settings.json"
        settings.parent.mkdir()
        settings.write_text('{"permissions":{"deny":["command(*)"],"ask":["read_url(*)"]}}')
        args=skills.agy_args(self.home,"demo",files,"model","question")
        rules=json.loads(args[args.index("--settings")+1])["permissions"]
        self.assertEqual(rules["deny"],["command(*)"])
        self.assertEqual(rules["ask"],["read_url(*)"])
        self.assertEqual(rules["allow"],[f"read_file({directory/n})" for n in ["SKILL.md","ref"]])
        with self.assertRaises(ValueError): skills.agy_args(self.home,"unlisted",files,"model","question")
