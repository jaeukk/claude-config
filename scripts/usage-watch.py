#!/usr/bin/env python3
"""Terminal token-usage watcher: Claude Code + Codex quota bars.

Renders ANSI truecolor bar cards matching the token-usage widget: usage fill
(green <50%, amber >=50%, red >=90%), blue period-progress tick, grey track.
Zero LLM involvement — data comes from the Claude OAuth usage API and the
newest Codex session rate_limits snapshot.

Usage:
  usage-watch.py            # live view, refresh every 30 s (Ctrl-C to quit)
  usage-watch.py --once     # print one snapshot and exit
  usage-watch.py -n 10      # custom refresh interval (seconds)
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
import time
import unicodedata
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
WINDOW_HOURS = {"5h": 5, "7d": 168}
BAR_WIDTH = 40

# palette (matches the web widget)
GREEN = (95, 191, 95)
AMBER = (214, 159, 44)
RED = (229, 83, 75)
BLUE = (74, 144, 226)
TRACK = (74, 82, 96)
MUTED = (138, 148, 166)
TEXT = (230, 234, 242)


def fg(rgb, s, bold=False):
    r, g, b = rgb
    pre = "\033[1m" if bold else ""
    return f"{pre}\033[38;2;{r};{g};{b}m{s}\033[0m"


def sev_color(pct):
    return RED if pct >= 90 else AMBER if pct >= 50 else GREEN


def fmt_left(delta):
    secs = max(0, int(delta.total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h" if d > 0 else f"{h}h {m}m"


def disp_w(s):
    """Terminal display width (Hangul and other wide glyphs count as 2 columns)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad_to(s, w):
    return s + " " * max(0, w - disp_w(s))


def parse_iso(s):
    """ISO timestamp → aware KST datetime; naive inputs are treated as UTC."""
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(KST)


def fmt_ago(then, now):
    mins = int((now - then).total_seconds() // 60)
    if mins < 1:
        return "방금"
    if mins < 60:
        return f"{mins}m 전"
    return f"{mins // 60}h {mins % 60}m 전"


def make_row(label, used_pct, resets_at, now, window_hours=None):
    hours = window_hours or WINDOW_HOURS.get(label.split()[-1], 168)
    window = dt.timedelta(hours=hours)
    left = resets_at - now
    period = 100.0 * (window - left).total_seconds() / window.total_seconds()
    return {
        "label": label,
        "usage": round(used_pct),
        "period": min(max(period, 0.0), 100.0),
        "reset": fmt_left(left),
    }


def claude_card(now):
    creds = json.load(open(os.path.expanduser("~/.claude/.credentials.json")))
    token = (creds.get("claudeAiOauth") or creds).get("accessToken")
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01",
        },
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    rows = []
    for lim in resp.get("limits", []):
        try:
            kind = lim.get("kind", "")
            if kind == "session":
                label, hours = "5h", 5
            elif kind == "weekly_all":
                label, hours = "7d", 168
            elif kind.startswith("weekly"):
                model = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
                label, hours = f"{model} 7d", 168
            else:
                continue
            rows.append(make_row(label, lim["percent"], parse_iso(lim["resets_at"]), now, hours))
        except (KeyError, TypeError, ValueError):
            continue  # skip one malformed entry, keep the rest
    if not rows:
        raise RuntimeError(f"no usage windows in response; keys={list(resp)}")
    return {"provider": "Claude Code", "plan": None, "updated": "방금", "rows": rows}


def codex_card(now):
    base = os.path.expanduser("~/.codex/sessions")
    files = sorted(glob.glob(f"{base}/*/*/*/*.jsonl"), key=os.path.getmtime, reverse=True)[:15]

    def find(o):
        if isinstance(o, dict):
            if "rate_limits" in o:
                return o["rate_limits"]
            for v in o.values():
                r = find(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r:
                    return r
        return None

    for fn in files:
        best = None
        try:
            for line in open(fn, errors="ignore"):
                if "rate_limit" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rl = find(obj)
                if rl and rl.get("primary"):
                    best = (rl, obj.get("timestamp"))
        except OSError:
            continue  # file vanished or unreadable — try the next one
        if not best:
            continue
        rl, ts = best
        try:
            snap = parse_iso(ts) if ts else now
            rows = []
            for key, mins_default in (("primary", 10080), ("secondary", 300)):
                w = rl.get(key)
                if not w:
                    continue
                mins = w.get("window_minutes") or mins_default
                label = f"{mins // 1440}d" if mins >= 1440 else f"{mins // 60}h"
                resets = dt.datetime.fromtimestamp(w["resets_at"], tz=KST)
                rows.append(make_row(label, w["used_percent"], resets, now, mins / 60))
            if not rows:
                continue
            return {
                "provider": "Codex",
                "plan": (rl.get("plan_type") or "").capitalize() or None,
                "updated": fmt_ago(snap, now),
                "rows": rows,
            }
        except (KeyError, TypeError, ValueError, OSError):
            continue  # malformed snapshot — fall back to an older file
    raise RuntimeError("no Codex rate_limits snapshot found")


def bar(usage, period):
    fill_n = round(usage / 100 * BAR_WIDTH)
    tick_i = min(round(period / 100 * (BAR_WIDTH - 1)), BAR_WIDTH - 1)
    color = sev_color(usage)
    cells = []
    for i in range(BAR_WIDTH):
        if i == tick_i:
            cells.append(fg(BLUE, "▐", bold=True))
        elif i < fill_n:
            cells.append(fg(color, "█"))
        else:
            cells.append(fg(TRACK, "░"))
    return "".join(cells)


def render_card(card):
    lines = []
    left_plain = f"{card['provider']}  최신" + (f" {card['plan']}" if card.get("plan") else "")
    head = fg(TEXT, card["provider"], bold=True) + "  " + fg(GREEN, "최신")
    if card.get("plan"):
        head += " " + fg(BLUE, card["plan"])
    head += " " * max(1, 64 - disp_w(left_plain)) + fg(MUTED, f"{card['updated']} 갱신")
    lines.append(head)
    label_w = max(disp_w(r["label"]) for r in card["rows"]) + 2
    for r in card["rows"]:
        pct = fg(sev_color(r["usage"]), f"{r['usage']:>3}%", bold=True)
        reset = fg(MUTED, f"↻ {r['reset']}")
        lines.append(f"  {fg(TEXT, pad_to(r['label'], label_w))}{bar(r['usage'], r['period'])}  {pct}  {reset}")
    return "\n".join(lines)


def snapshot():
    now = dt.datetime.now(KST)
    out, errors = [], []
    for fn in (claude_card, codex_card):
        try:
            out.append(render_card(fn(now)))
        except Exception as e:  # keep the other card alive
            errors.append(f"{fn.__name__}: {e}")
    body = "\n\n".join(out)
    if errors:
        body += "\n" + fg(RED, "⚠ " + "; ".join(errors))
    stamp = fg(MUTED, now.strftime("%Y-%m-%d %H:%M:%S KST"))
    return f"{stamp}\n\n{body}\n"


def main():
    ap = argparse.ArgumentParser(description="Claude/Codex token-usage bars in the terminal")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("-n", "--interval", type=float, default=30, help="refresh seconds (default 30)")
    args = ap.parse_args()

    if args.once:
        print(snapshot())
        return
    try:
        sys.stdout.write("\033[?25l")  # hide cursor
        while True:
            frame = snapshot()
            sys.stdout.write("\033[2J\033[H" + frame + fg(MUTED, "\nCtrl-C to quit") + "\n")
            sys.stdout.flush()
            time.sleep(max(2.0, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")  # restore cursor
        sys.stdout.flush()


if __name__ == "__main__":
    main()
