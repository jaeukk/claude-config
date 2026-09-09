#!/usr/bin/env python3
"""Claude Code status line: three stacked lines (plus wrap).

  1. user@host:/cwd │  branch*            (PS1 prefix + git branch, * = dirty)
  2. model · effort │ ctx used │ out │ pony:<mode> │ $cost
  3. skills: every skill invoked this session (wraps onto line 4+ when long)

Quota bars were retired 2026-09-08 (Orca shows them natively); the previous
version is in ~/.cache/claude-backups/statusline.py.2026-09-08-quota-bars and
git history of claude-config.

Every line stays within COLUMNS - RESERVE: Claude Code lays its own footer pills
(/rc, mode labels) beside the status line in a wrapping row, and a line that is
too wide pushes them underneath.

Status JSON schema (subset we use):
  { "model": {"id", "display_name"}, "effort": {"level"},
    "transcript_path": "...",
    "cost": {"total_cost_usd"},
    "context_window": {"context_window_size": int, "current_usage": {...}} }
"""
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
import unicodedata
from getpass import getuser

RESERVE = 16  # columns to leave free for Claude Code's footer pills


def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def _ps1_prefix(cwd: str) -> str:
    """Render 'user@host:/path' in the same green/blue style as the shell PS1."""
    user = getuser()
    host = socket.gethostname().split(".")[0]
    user_host = _ansi("01;32", f"{user}@{host}")
    path = _ansi("01;34", cwd or "")
    return f"{user_host}:{path}"


def _git_branch(cwd: str) -> str:
    """'branch' (plus '*' when the tree is dirty), or '' outside a git repo."""
    if not cwd:
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain=v2", "--branch",
             "--untracked-files=no"],
            capture_output=True, text=True, timeout=1,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    branch, dirty = "", False
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif not line.startswith("#"):
            dirty = True
    return f"{branch}{'*' if dirty else ''}" if branch else ""


def _vis_w(text: str) -> int:
    """Terminal columns a rendered string occupies (ANSI stripped, wide glyphs = 2)."""
    plain = re.sub(r"\033\[[0-9;]*m", "", text)
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in plain)


def _fit(parts: list, drop_order: tuple, budget: int) -> str:
    """Join (name, text) segments, dropping the least important until it fits budget."""
    parts = list(parts)
    line = _ansi("90", " │ ").join(t for _, t in parts)
    for name in drop_order:
        if _vis_w(line) <= budget:
            break
        parts = [(n, t) for n, t in parts if n != name]
        line = _ansi("90", " │ ").join(t for _, t in parts)
    return line


def _human(n: int) -> str:
    """Compact token count, e.g. 45300 -> '45.3k', 1200000 -> '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _ponytail_mode() -> str:
    """Active ponytail level from its flag file; '' when off, unset, or unreadable.

    Bounded read + allowlist: the file is written by a third-party plugin hook, so
    anything unexpected (ANSI escapes, a huge blob, bad UTF-8) must not reach the
    status line. 'off' deliberately renders no badge.
    """
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    try:
        with open(os.path.join(claude_dir, ".ponytail-active"), encoding="utf-8") as fh:
            mode = fh.read(16).strip().lower()
    except (OSError, ValueError):  # ValueError covers UnicodeDecodeError
        return ""
    return mode if mode in ("lite", "full", "ultra") else ""


def _context_limit(data: dict, model_id: str) -> int:
    """Context window size, read from the status JSON when available."""
    size = (data.get("context_window") or {}).get("context_window_size")
    if isinstance(size, int) and size > 0:
        return size
    mid = (model_id or "").lower()
    if "[1m]" in mid or "1m" in mid:
        return 1_000_000
    return 200_000


_CMD = re.compile(r"<command-name>/([\w:.-]+)</command-name>")


# Skills whose instructions stay in effect for the rest of the session. One-shot
# actions (organizer, finding-unknowns, eli5, ponytail-audit, …) are left out on
# purpose: the list exists to show what is already loaded, not what was run.
PERSISTENT_SKILLS = {
    "caveman-skill", "ponytail", "cpp-guidelines", "python-guidelines",
    "karpathy-guidelines", "zotero-obsidian-sync", "multiagent", "orchestration",
    "orca-cli", "computer-use",
}


def _scan_transcript(transcript_path: str):
    """(latest assistant `message.usage`, skills invoked in order) from the transcript.

    Skills arrive two ways: as `Skill` tool_use blocks (model-invoked) and as
    `<command-name>/x</command-name>` in user turns (typed slash commands). Only
    PERSISTENT_SKILLS are kept.

    The last turn's context size is input_tokens + cache_read + cache_creation
    (all tokens sent to the model for that turn); output_tokens is the reply.
    """
    if not transcript_path:
        return None, []
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None, []

    usage, skills, known = None, [], PERSISTENT_SKILLS
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for name in _CMD.findall(line):  # slash-invoked skills land as <command-name> in user turns
            if name.rsplit(":", 1)[-1] in known and name not in skills:
                skills.append(name)
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        u = msg.get("usage")
        if isinstance(u, dict) and u.get("input_tokens") is not None:
            usage = u  # keep overwriting: the last one wins
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") == "Skill"):
                    name = (block.get("input") or {}).get("skill")
                    if (isinstance(name, str) and name.rsplit(":", 1)[-1] in known
                            and name not in skills):
                        skills.append(name)
    return usage, skills


def _skills_lines(skills: list, budget: int) -> list:
    """'skills: a, b, c' wrapped to the budget; a dim placeholder when none yet."""
    if not skills:
        return [_ansi("90", "skills: none")]
    label = "skills: "
    rows = textwrap.wrap(", ".join(skills), width=budget,
                         initial_indent=label, subsequent_indent=" " * len(label),
                         break_long_words=False, break_on_hyphens=False)
    return [_ansi("36", r) for r in rows]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    model = data.get("model") or {}
    model_name = model.get("display_name") or model.get("id") or "claude"
    effort = (data.get("effort") or {}).get("level")
    if effort:
        model_name = f"{model_name} · {effort}"
    model_id = model.get("id") or ""

    cost = data.get("cost") or {}
    total_cost = cost.get("total_cost_usd") or 0.0

    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd", "")

    sep = _ansi("90", " │ ")
    budget = (int(os.environ.get("COLUMNS") or 0) or 80) - RESERVE

    # line 1: where we are
    line1 = [_ps1_prefix(cwd)]
    branch = _git_branch(cwd)
    if branch:
        line1.append(_ansi("35", f" {branch}"))

    # line 2: model, context — compact, and trimmed to leave room for the pills
    usage, skills = _scan_transcript(data.get("transcript_path", ""))
    usage = (data.get("context_window") or {}).get("current_usage") or usage
    line2 = [("model", _ansi("1;36", model_name.replace(" context)", ")")))]
    if usage:
        ctx = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )
        out = usage.get("output_tokens") or 0
        limit = _context_limit(data, model_id)
        pct = (ctx / limit * 100) if limit else 0.0
        # color the context fraction by how full it is
        color = "32" if pct < 50 else ("33" if pct < 80 else "31")
        line2.append(("ctx", _ansi(color, f"ctx {_human(ctx)}/{_human(limit)} ({pct:.0f}%)")))
        line2.append(("out", _ansi("90", f"out {_human(out)}")))
    mode = _ponytail_mode()
    if mode:
        line2.append(("pony", _ansi("33", f"pony:{mode}")))
    line2.append(("cost", _ansi("90", f"${total_cost:.2f}")))

    # lines 3+: every skill invoked this session
    lines = [sep.join(line1), _fit(line2, ("out", "cost", "pony"), budget)]
    lines += _skills_lines(skills, budget)
    sys.stdout.write("\n".join(lines))


if __name__ == "__main__":
    main()
