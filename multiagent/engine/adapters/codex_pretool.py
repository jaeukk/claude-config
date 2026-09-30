"""Codex PreToolUse adapter for the local multi-agent policy engine.

Codex 0.159 runs ``PreToolUse`` hooks registered in ``~/.codex/hooks.json`` before every shell
command, ``apply_patch`` edit and sub-agent call, and a hook that prints the Claude-style
``permissionDecision: "deny"`` object blocks the call (probe of 2026-09-30,
``tasks/2026-09-30-steps-3-7``). This adapter translates a Codex event into the event shape
``claude_pretool.evaluate`` decides on, so both hosts apply one set of rules:

- a shell command (``Bash``) -> the same shell rules;
- ``apply_patch`` -> one write check per file the patch adds, updates, deletes or moves to;
- ``collaborationspawn_agent`` -> the native-spawn check, with the child's family ``codex``.

It acts only for a session whose working directory is inside this installation (the directory
holding ``engine/``) while ``tasks/.active-task`` names a task, like the Claude hook, which loads
only for sessions launched there. Everything else is allowed untouched.

Codex treats a hook that crashes, prints malformed output or times out as allowing the call, so
this adapter turns its own errors into a denial; a crash of the interpreter itself still fails
open. That is the engine's cooperative model: it catches a conductor's slip, it is not a boundary.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

_SPEC = importlib.util.spec_from_file_location(
    "claude_pretool", Path(__file__).resolve().parent / "claude_pretool.py"
)
hook = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hook)

#: Codex's names for a shell command; the command is ``tool_input.command``.
SHELL_TOOLS = frozenset({"Bash", "shell", "local_shell", "exec_command"})
#: The patch headers that name a file ``apply_patch`` will create, change, delete or move to.
PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)
#: Codex's sub-agent spawn; the other collaboration tools (wait, send, list) start nothing.
CODEX_SPAWN_TOOLS = frozenset({"collaborationspawn_agent", "spawn_agent"})


def in_scope(event: dict[str, Any]) -> bool:
    """Whether the session's working directory is inside this installation."""
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        where = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return False
    return where == hook.ROOT or hook.ROOT in where.parents


def patch_paths(patch: str, cwd: Path) -> list[str]:
    """Absolute paths an ``apply_patch`` body touches, relative ones resolved against ``cwd``."""
    paths = []
    for name in PATCH_PATH.findall(patch):
        path = Path(name.strip())
        paths.append(str(path if path.is_absolute() else cwd / path))
    return paths


def translate(event: dict[str, Any]) -> list[tuple[dict[str, Any], str | None]]:
    """The Claude-shaped events one Codex event amounts to, each with its spawn family.

    Raises ``ValueError`` for an ``apply_patch`` whose files cannot be read from the patch, so
    the caller refuses it rather than letting an unchecked edit through.
    """
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    base = {key: event[key] for key in ("agent_id",) if event.get(key)}
    if tool in SHELL_TOOLS:
        command = tool_input.get("command", "")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return [({**base, "tool_name": "Bash", "tool_input": {"command": str(command)}}, None)]
    if tool == "apply_patch":
        patch = tool_input.get("command") or tool_input.get("patch") or tool_input.get("input") or ""
        paths = patch_paths(str(patch), Path(str(event.get("cwd", "."))))
        if not paths:
            raise ValueError("apply_patch names no file this adapter can check")
        return [({**base, "tool_name": "Edit", "tool_input": {"file_path": path}}, None)
                for path in paths]
    if tool in CODEX_SPAWN_TOOLS:
        return [({**base, "tool_name": "Agent", "tool_input": {}}, "codex")]
    return []


def main() -> int:
    """Evaluate one Codex hook event from standard input."""
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict) or not in_scope(event):
            return 0
        if hook.active_task_path() is None:
            return 0  # no task is active: nothing to enforce (an invalid pointer raises and denies)
        for claude_event, family in translate(event):
            reason = hook.evaluate(claude_event, spawn_family=family)
            if reason:
                hook.deny(reason)
                return 0
        return 0
    except Exception as error:  # the adapter's own errors deny; Codex would otherwise allow
        hook.deny(f"multi-agent policy hook (Codex) failed closed: {error}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
