#!/usr/bin/env python3
"""Claude Code status line: three stacked lines.

  1. user@host:/cwd │  branch*            (PS1 prefix + git branch, * = dirty)
  2. model │ ctx used │ out │ pony:<mode> │ skills invoked this session │ $cost
  3. Claude <bar> <pct> · <reset> left   (5h window)
  4. Codex  <bar> <pct> · <reset> left   (5h window)

The bars carry a blue tick showing how far the 5h window itself has run, so
fill behind the tick means the budget is outlasting the clock.

Line 3 reuses the quota readers in ~/.claude/scripts/usage-watch.py, but those
cost an HTTPS round trip and a `codex app-server` spawn, so the values are kept
in a small JSON cache refreshed by a detached `--refresh-usage` run of this same
file. Rendering never blocks on the network.

Status JSON schema (subset we use):
  { "model": {"id", "display_name"},
    "transcript_path": "...",
    "cost": {"total_cost_usd", "total_lines_added", "total_lines_removed"},
    "context_window": {"context_window_size": int} }
"""
import importlib.util
import itertools
import json
import os
import re
import socket
import subprocess
import sys
import time
import unicodedata
from getpass import getuser

CACHE = os.path.expanduser("~/.cache/claude-usage-5h.json")
BAR_W = 28  # bar cells
WINDOW = 5 * 3600  # the 5h quota window, in seconds
GREEN, AMBER, RED = (95, 191, 95), (214, 159, 44), (229, 83, 75)
BLUE, TRACK = (74, 144, 226), (74, 82, 96)
# Claude Code lays its own footer pills (/rc, mode labels) beside the status line
# in a wrapping row; if our widest line leaves no room they wrap underneath.
RESERVE = 16  # columns to leave free for those pills
CACHE_TTL = 180  # seconds before the cached quota is considered stale
SPAWN_TTL = 60  # min seconds between background refresh spawns
USAGE_WATCH = os.path.expanduser("~/.claude/scripts/usage-watch.py")


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


def _scan_transcript(transcript_path: str):
    """(latest assistant `message.usage`, skills invoked in order) from the transcript.

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

    usage, skills = None, []
    for line in lines:
        line = line.strip()
        if not line:
            continue
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
                    if isinstance(name, str) and name not in skills:
                        skills.append(name)
    return usage, skills


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _stale(path: str, ttl: float) -> bool:
    try:
        return time.time() - os.path.getmtime(path) > ttl
    except OSError:
        return True


def _spawn_refresh() -> None:
    """Kick off a detached quota refresh, at most once per SPAWN_TTL."""
    lock = CACHE + ".lock"
    if not _stale(lock, SPAWN_TTL):
        return
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        open(lock, "w").close()  # touch: throttles the next spawn
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh-usage"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except (OSError, ValueError):
        pass


def refresh_usage() -> None:
    """Write Codex's 5h quota to CACHE (background entry point).

    Claude's own window comes free on stdin, so only Codex needs fetching.
    """
    spec = importlib.util.spec_from_file_location("usage_watch", USAGE_WATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        rows = (mod.codex_card(mod.dt.datetime.now(mod.KST)) or {}).get("rows") or []
    except Exception:  # codex missing, not logged in, malformed payload
        return
    for row in rows:
        if row.get("label") == "5h":
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as fh:
                json.dump({"codex": {"pct": row["usage"], "period": row["period"],
                                  "reset": row["reset"]}}, fh)
            return


def _fmt_left(seconds: float) -> str:
    h, m = divmod(max(0, int(seconds)) // 60, 60)
    return f"{h}h {m:02d}m"


def _rgb(color, text: str) -> str:
    r, g, b = color
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def _bar(pct: float, period: float) -> str:
    """A BAR_W-cell usage bar with a blue tick marking how far the window has run.

    Fill left of the tick means the budget is being spent slower than the clock.
    """
    pct = min(max(pct, 0.0), 100.0)
    filled = round(BAR_W * pct / 100)
    tick = min(BAR_W - 1, int(BAR_W * min(max(period, 0.0), 100.0) / 100))
    color = RED if pct >= 90 else (AMBER if pct >= 50 else GREEN)
    cells = [
        (BLUE, "┃") if i == tick
        else (color if i < filled else TRACK, "█" if i < filled else "░")
        for i in range(BAR_W)
    ]
    # merge runs of one color into a single escape sequence
    return "".join(_rgb(c, "".join(ch for _, ch in g))
                   for c, g in itertools.groupby(cells, key=lambda cell: cell[0]))


def _quota_rows(data: dict):
    """[(name, pct, period, reset)] for the 5h windows of Claude Code and Codex.

    Claude's window rides in on stdin; only Codex needs the cached background
    fetch (see refresh_usage).
    """
    if _stale(CACHE, CACHE_TTL):
        _spawn_refresh()
    rows = []

    five = (data.get("rate_limits") or {}).get("five_hour") or {}
    if five.get("used_percentage") is not None:
        # absent on the first renders of a fresh session, until the first API reply
        left = (five.get("resets_at") or 0) - time.time()
        period = 100.0 * (WINDOW - min(max(left, 0), WINDOW)) / WINDOW
        rows.append(("Claude", five["used_percentage"], period, _fmt_left(left)))
    else:
        rows.append(("Claude", 0, 0.0, None))

    codex = _load_cache().get("codex")
    if codex:
        # reset/period are as of the cache write: <=CACHE_TTL of drift on a 5h window
        rows.append(("Codex", codex["pct"], codex["period"], codex["reset"]))
    else:
        rows.append(("Codex", 0, 0.0, None))
    return rows


def _quota_lines(data: dict):
    out = []
    for name, pct, period, reset in _quota_rows(data):
        if reset is None:
            out.append(f"{_ansi('90', name.ljust(6))} {_rgb(TRACK, '░' * BAR_W)} {_ansi('90', '  --')}")
            continue
        color = "31" if pct >= 90 else ("33" if pct >= 50 else "32")
        out.append(
            f"{_ansi('1', name.ljust(6))} {_bar(pct, period)} "
            f"{_ansi(color, f'{pct:>3.0f}%')} {_ansi('90', f'↻ {reset}')}"
        )
    return out


def main() -> None:
    if "--refresh-usage" in sys.argv[1:]:
        refresh_usage()
        return

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

    # line 1: where we are
    line1 = [_ps1_prefix(cwd)]
    branch = _git_branch(cwd)
    if branch:
        line1.append(_ansi("35", f" {branch}"))

    # line 2: model, context, skills — compact, and trimmed to leave room for the pills
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
    if skills:
        line2.append(("skills", _ansi("36", "skills: " + ", ".join(skills[-4:]))))
    line2.append(("cost", _ansi("90", f"${total_cost:.2f}")))

    budget = (int(os.environ.get("COLUMNS") or 0) or 80) - RESERVE
    lines = [sep.join(line1), _fit(line2, ("out", "skills", "cost", "pony"), budget)]
    sys.stdout.write("\n".join(lines + _quota_lines(data)))


if __name__ == "__main__":
    main()
