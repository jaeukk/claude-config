#!/usr/bin/env python3
"""fable layer 4 — PreToolUse orchestration gate.

Physically limits the MAIN agent to <= 2 direct code-file edits per user turn and
blocks Bash-based code edits (sed -i, perl -i, redirection, tee) unconditionally.
Subagent tool calls (payload carries agent_id/agent_type) are always allowed —
delegation IS the execution path. Turn boundaries are detected via prompt_id.

Registered in ~/.claude/settings.json under PreToolUse with matcher
"Write|Edit|MultiEdit|NotebookEdit|Bash". Exit 2 blocks the call and returns
stderr to the model; any unhandled exception exits 0 (fail-open).
"""
import json
import os
import re
import sys
import tempfile

FABLE_DIR = os.path.expanduser("~/.claude/fable")
MAX_FILES_PER_TURN = 2

CODE_EXTS = {
    ".py", ".ipynb", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".cu", ".cuh",
    ".rs", ".go", ".java", ".kt", ".swift", ".scala", ".hs",
    ".sh", ".bash", ".zsh", ".pl", ".pm", ".rb", ".php", ".lua", ".jl",
    ".f", ".f90", ".f95", ".m", ".mm", ".r", ".sql",
}

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def is_code_file(path):
    return os.path.splitext(path)[1].lower() in CODE_EXTS


def deny(message):
    sys.stderr.write(message)
    sys.exit(2)


def bash_code_edit(command):
    """Detect the common Bash bypasses that would write to a code file."""
    targets = []
    # output redirection:  > file  /  >> file   (skip fd-redirects like 2>&1)
    for m in re.finditer(r">>?\s*['\"]?([^\s'\">|;&]+)", command):
        targets.append(m.group(1))
    # tee [-a] file...
    for m in re.finditer(r"\btee\b\s+((?:-\w+\s+)*)([^\s|;&]+)", command):
        targets.append(m.group(2))
    if any(is_code_file(t) for t in targets):
        return True
    # in-place editors: sed -i / perl -i touching a code-file argument
    if re.search(r"\b(sed|perl)\b[^|;&]*\s-[a-zA-Z]*i", command):
        tokens = re.findall(r"[^\s'\"|;&]+", command)
        if any(is_code_file(t) for t in tokens):
            return True
    return False


def turn_state_path(session_id):
    d = os.path.join(tempfile.gettempdir(), "fable-gate-%d" % os.getuid())
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.json" % session_id)


def main():
    payload = json.load(sys.stdin)

    # Switch off -> every check is skipped (fail-through).
    try:
        with open(os.path.join(FABLE_DIR, "state")) as f:
            state = f.read().strip()
    except OSError:
        state = "off"
    if state != "on":
        return

    # Subagent exemption: agent_id/agent_type are present only inside subagents.
    if payload.get("agent_id") or payload.get("agent_type"):
        return

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool == "Bash":
        command = tool_input.get("command", "")
        if bash_code_edit(command):
            deny(
                "[fable gate] Editing code files via Bash (sed -i, perl -i, "
                "redirection, tee) is blocked for the main agent. Use Edit/Write "
                "for small changes (max 2 code files per turn) or delegate the "
                "edit to a subagent (general-purpose with model: opus)."
            )
        return

    if tool not in EDIT_TOOLS:
        return

    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not path or not is_code_file(path):
        return  # non-code files are never limited

    path = os.path.abspath(path)
    session_id = payload.get("session_id", "unknown")
    prompt_id = payload.get("prompt_id")

    state_file = turn_state_path(session_id)
    try:
        with open(state_file) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}

    # New user prompt -> reset the per-turn file counter.
    if data.get("prompt_id") != prompt_id:
        data = {"prompt_id": prompt_id, "files": []}

    files = data.get("files", [])
    if path in files:
        return  # re-editing an already-counted file is free

    if len(files) >= MAX_FILES_PER_TURN:
        deny(
            "[fable gate] Direct code-edit limit reached (%d code files this "
            "turn: %s). You are the conductor — delegate this edit instead of "
            "retrying: implementation/tests -> general-purpose subagent with "
            "model: opus; hard design/root-cause -> deep-reasoner; chores -> "
            "runner." % (len(files), ", ".join(os.path.basename(p) for p in files))
        )

    files.append(path)
    data["files"] = files
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, state_file)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail-open: a gate bug must never freeze the session
