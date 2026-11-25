#!/usr/bin/env python3
"""Run headless agy or a read-only multiagent producer with one selected skill."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile


def selected_files(home: Path, name: str, refs: list[str]) -> dict[Path, Path]:
    """Resolve named regular files inside a single known skill tree."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid skill name")
    entry = home/".claude/skills"/name
    root = entry.resolve(strict=True)
    if root not in (entry, home/".agents/skills"/name):
        raise ValueError(f"Unexpected skill target: {root}")
    files = {}
    for filename in ("SKILL.md", *refs):
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid reference: {relative}")
        path = root/relative
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError(f"Expected a regular skill file: {path}")
        if any(ch in str(path) for ch in "*?[]()\n\r"):
            raise ValueError(f"Unsupported permission-rule character in {path}")
        files[relative] = path
    return files


def agy_args(home: Path, name: str, files: dict[Path, Path], model: str, prompt: str) -> list[str]:
    """Add exact read grants for a skill exposed by the managed agy manifest."""
    doc = json.loads((home/".gemini/config/skills.json").read_text())
    enabled = any(Path(e["path"]).expanduser() == home/".claude/skills" and
                  name in e.get("include_only", []) and name not in e.get("exclude", [])
                  for e in doc.get("entries", []))
    if not enabled or name in doc.get("exclude", []):
        raise ValueError(f"{name} is not exposed to agy")
    config = home/".gemini/antigravity-cli/settings.json"
    permissions = json.loads(config.read_text()).get("permissions", {}) if config.exists() else {}
    allow = list(permissions.get("allow", []))
    allow.extend(f"read_file({p})" for p in files.values())
    settings = {"permissions": {**permissions, "allow": list(dict.fromkeys(allow))}}
    return ["agy", "--mode", "plan", "--model", model, "--settings", json.dumps(settings), "-p", prompt]


def _run_producer(args):
    """Forward interruption to the process group before releasing its temporary inputs."""
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        child = subprocess.Popen(args, start_new_session=True)
        try:
            return child.wait()
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=5)
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            raise
    finally:
        signal.signal(signal.SIGTERM, previous)


def main() -> int:
    """Dispatch a client with its existing account, permission and output controls."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="client", required=True)
    agy = commands.add_parser("agy")
    agy.add_argument("--model", required=True)
    agy.add_argument("--prompt", required=True)
    worker = commands.add_parser("produce")
    for arg in ("brief","target-repo","out"):
        worker.add_argument("--"+arg, type=Path, required=True)
    worker.add_argument("--tasks-root", type=Path)
    worker.add_argument("--conductor-host", choices=["codex","claude-code"], required=True)
    for child in (agy,worker):
        child.add_argument("--skill", required=True)
        child.add_argument("--reference", action="append", default=[])
    opts = parser.parse_args()
    home = Path.home()
    files = selected_files(home,opts.skill,opts.reference)
    if opts.client == "agy":
        args = agy_args(home,opts.skill,files,opts.model,opts.prompt)
        os.execvp(args[0],args)
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)/opts.skill
        for relative, source in files.items():
            target = bundle/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source,target)
            target.chmod(0o400)
        brief = Path(tmp)/"worker-brief.txt"
        brief.write_text(opts.brief.read_text() + f"\nUse the requested skill at {bundle/'SKILL.md'}. Read its references relative to that file.\n")
        engine = Path(__file__).resolve().parents[1]/"multiagent/engine/policy_engine.py"
        args = [sys.executable,str(engine),"produce","--target-repo",str(opts.target_repo),
                "--brief",str(brief),"--out",str(opts.out),"--conductor-host",opts.conductor_host,
                "--read-scope",str(bundle)]
        if opts.tasks_root:
            args.extend(["--tasks-root",str(opts.tasks_root)])
        return _run_producer(args)


if __name__ == "__main__":
    raise SystemExit(main())
