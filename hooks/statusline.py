#!/usr/bin/env python3
"""Claude Code status line: show model, context-token usage, and session cost.

Reads the status JSON that Claude Code feeds on stdin, then parses the session
transcript (JSONL) to recover the most recent token-usage record so the line
reflects live context consumption. Output is a single line (ANSI-colored).

Status JSON schema (subset we use):
  { "model": {"id", "display_name"},
    "transcript_path": "...",
    "cost": {"total_cost_usd", "total_lines_added", "total_lines_removed"},
    "exceeds_200k_tokens": bool }
"""
import json
import sys


def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def _human(n: int) -> str:
    """Compact token count, e.g. 45300 -> '45.3k', 1200000 -> '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _context_limit(model_id: str, exceeds_200k: bool) -> int:
    """Best-effort context window from the model id."""
    mid = (model_id or "").lower()
    if "[1m]" in mid or "1m" in mid:
        return 1_000_000
    return 200_000


def _latest_usage(transcript_path: str):
    """Return the most recent assistant `message.usage` dict, or None.

    The last turn's context size is input_tokens + cache_read + cache_creation
    (all tokens sent to the model for that turn); output_tokens is the reply.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = (rec.get("message") or {}).get("usage")
        if isinstance(usage, dict) and usage.get("input_tokens") is not None:
            return usage
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    model = data.get("model") or {}
    model_name = model.get("display_name") or model.get("id") or "claude"
    model_id = model.get("id") or ""

    cost = data.get("cost") or {}
    total_cost = cost.get("total_cost_usd") or 0.0

    usage = _latest_usage(data.get("transcript_path", ""))
    parts = [_ansi("1;36", model_name)]  # bold cyan model name

    if usage:
        ctx = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )
        out = usage.get("output_tokens") or 0
        limit = _context_limit(model_id, bool(data.get("exceeds_200k_tokens")))
        pct = (ctx / limit * 100) if limit else 0.0
        # color the context fraction by how full it is
        color = "32" if pct < 50 else ("33" if pct < 80 else "31")
        ctx_str = f"ctx {_human(ctx)}/{_human(limit)} ({pct:.0f}%)"
        parts.append(_ansi(color, ctx_str))
        parts.append(_ansi("90", f"out {_human(out)}"))

    parts.append(_ansi("90", f"${total_cost:.4f}"))

    sys.stdout.write(_ansi("90", " │ ").join(parts))


if __name__ == "__main__":
    main()
