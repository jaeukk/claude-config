"""Check that compose-settings.py keeps local edits and still applies upstream changes.

Run: python3 -m unittest ~/.claude/scripts/test_compose_settings.py
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "compose_settings", Path(__file__).with_name("compose-settings.py"))
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


class LocalEdits(unittest.TestCase):
    def test_patch_reproduces_live(self):
        baseline = {"a": 1, "hooks": {"Stop": [1], "Start": [2]}, "gone": 0}
        live = {"a": 2, "hooks": {"Stop": [1, 3], "Start": [2]}, "new": True}
        patch, removed = cs.local_edits(baseline, live)
        self.assertEqual(patch, {"a": 2, "hooks": {"Stop": [1, 3]}, "new": True})
        self.assertEqual(removed, ["gone"])

    def test_nested_deletion_is_reported(self):
        _, removed = cs.local_edits({"m": {"k": 1, "j": 2}}, {"m": {"k": 1}})
        self.assertEqual(removed, ["m.j"])


class ThreeWay(unittest.TestCase):
    def test_upstream_change_applies_and_local_edit_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = (cs.CLAUDE, cs.SNAPSHOT, os.environ.get("HOME"))
            cs.CLAUDE, cs.SNAPSHOT = root, root / "settings.json.composed"
            os.environ["HOME"] = tmp  # no ~/.claude-team here, so the team write is skipped
            try:
                write = lambda name, d: (root / name).write_text(json.dumps(d))
                read = lambda name: json.loads((root / name).read_text())
                write("settings.base.json", {"a": 1, "b": 1})
                write("settings.json", {"a": 1, "b": 1})
                self.assertEqual(cs_main(), 0)  # first run: no snapshot yet
                write("settings.base.json", {"a": 1, "b": 2})  # upstream change (git pull)
                write("settings.json", {"a": 5, "b": 1})       # local edit (/config, Orca)
                self.assertEqual(cs_main(), 0)
                self.assertEqual(read("settings.json"), {"a": 5, "b": 2})
                self.assertEqual(read("settings.machine-local.json"), {"a": 5})
            finally:
                cs.CLAUDE, cs.SNAPSHOT = old[0], old[1]
                if old[2] is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old[2]


def cs_main() -> int:
    """Run the composer for the wsl machine layer, as update-config does on WSL."""
    import sys
    argv, sys.argv = sys.argv, ["compose-settings.py", "wsl"]
    try:
        return cs.main()
    finally:
        sys.argv = argv


if __name__ == "__main__":
    unittest.main()
