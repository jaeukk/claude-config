#!/usr/bin/env python3
"""Install or restore the shared global instruction links on WSL."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid


LEGACY_CODEX = "33a46668626cc7e3905eed85c5213189b94e8ee246f88f09502ea54e0ae59281"
LEGACY_GEMINI = "23cb3badc191127b30f1019881c52ade0165d5c561ea25bd8f06b1fbce201562"
ENTRIES = (".agents/AGENTS.md", ".codex/AGENTS.md", ".gemini/AGENTS.md", ".gemini/GEMINI.md")


def snapshot(path: Path) -> dict:
    """Capture an instruction file, its link target, or its absence."""
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"Expected an instruction file: {path}")
    return {"bytes": base64.b64encode(path.read_bytes()).decode(),
            "mode": stat.S_IMODE(path.stat().st_mode)}


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(path: Path, state: dict) -> None:
    """Publish one complete file state and sync the containing directory."""
    if not state:
        path.unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            new = Path(tmp) / "entry"
            if "link" in state:
                new.symlink_to(state["link"])
            else:
                new.write_bytes(base64.b64decode(state["bytes"]))
                new.chmod(state["mode"])
                with new.open("rb") as f:
                    os.fsync(f.fileno())
            new.replace(path)
    _sync(path.parent)


def plan(home: Path) -> dict:
    """Preflight all entries before changing any global instruction file."""
    source = home / ".claude/multiagent/global-instructions.md"
    if not source.is_file():
        raise ValueError(f"Missing shared instructions: {source}")
    with source.open(encoding="utf-8") as stream:
        rules = stream.read(8193)
    if not rules.strip() or len(rules) > 8192:
        raise ValueError("Shared instructions must have 1–8192 characters")
    if (home/".codex/AGENTS.override.md").is_file() and (home/".codex/AGENTS.override.md").stat().st_size:
        raise ValueError("Active global Codex override would hide the shared instructions")
    changes = {}
    for name in ENTRIES:
        path = home/name
        if path.parent.resolve() != path.parent:
            raise ValueError(f"Unexpected symlinked profile: {path.parent}")
        before = snapshot(path)
        desired = {"link": str(source)}
        digest = hashlib.sha256(base64.b64decode(before.get("bytes", ""))).hexdigest()
        if name.endswith("GEMINI.md"):
            if digest != LEGACY_GEMINI:
                continue  # Preserve other personal Gemini supplements.
            desired = {}
        elif path.is_symlink() and path.resolve() == source.resolve():
            continue
        elif before and not (name == ".codex/AGENTS.md" and digest == LEGACY_CODEX):
            raise ValueError(f"Resolve divergent instruction entry before installing: {path}")
        changes[name] = {"before": before, "after": desired}
    return changes


def transition(home: Path, changes: dict, side: str) -> None:
    """Allow re-entry after interruption and preserve external edits."""
    if set(changes) - set(ENTRIES):
        raise ValueError("Unexpected journal entry")
    items = list(changes.items())
    if side == "before":
        items.reverse()
    def desired(name, states):
        path = home/name
        if path.parent.resolve() != path.parent:
            raise ValueError(f"Unexpected symlinked profile: {path.parent}")
        current = snapshot(path)
        if side == "before" and name.endswith("GEMINI.md") and "bytes" in current and current != states["before"]:
            return None  # Keep the newly authored supplement. The archived journal retains the legacy one.
        if current and current not in (states["before"], states["after"]):
            raise ValueError(f"External edit at {path}; move it aside before retrying")
        return states[side] if current != states[side] else None

    for name, states in items:
        desired(name, states)
    for name, states in items:
        target = desired(name, states)
        if target is not None:
            path = home/name
            path.parent.mkdir(parents=True, exist_ok=True)
            replace(path, target)


def manage(home: Path, action: str) -> None:
    """Plan, apply, or undo the instruction migration under a single-writer lock."""
    if action == "check":
        print(json.dumps(list(plan(home))))
        return
    state = home/".local/state/shared-global-instructions"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal = state/"install.json"
    with (state/"lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recorded = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {}
        if action == "rollback":
            if not recorded:
                print("No active installation")
                return
            transition(home, recorded, "before")
            journal.rename(state/f"restored-{uuid.uuid4().hex}.json")
            _sync(state)
            return

        pending = plan(home)
        if not pending:
            return
        # Keep the original before-image across interrupted installs and later repairs.
        new = {**pending, **recorded}
        if new != recorded:
            data = json.dumps(new).encode()
            replace(journal, {"bytes":base64.b64encode(data).decode(), "mode":0o600})
        transition(home, pending, "after")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=["check", "apply", "rollback"])
    manage(Path.home(), cli.parse_args().action)
