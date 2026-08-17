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

The Antigravity (Gemini) quota is re-read from agy whenever its cache is older
than AGY_MAX_AGE; `--agy-max-age 0` keeps whatever agy last reported.
"""
import argparse
import datetime as dt
import glob
import json
import os
import queue
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
WINDOW_HOURS = {"5h": 5, "7d": 168}
BAR_WIDTH = 40

# Gemini/Antigravity: the 5h and weekly windows agy's own /usage shows. There is
# no standalone API for them (retrieveUserQuotaSummary is 403-gated to
# Antigravity's OAuth client), so we read the payload agy hands to its custom
# status line: ~/.claude/scripts/agy-statusline.py caches it here on every agy
# state change. The cache only moves while agy runs, so a stale one is refreshed
# automatically (see maybe_refresh_agy); `--refresh-agy` forces it.
AGY_CACHE = os.path.expanduser("~/.cache/agy-usage.json")
AGY_NAMES = {"gemini": "Gemini", "3p": "3rd-party"}
AGY_WINDOWS = {"5h": ("5h", 5), "weekly": ("7d", 168), "daily": ("1d", 24)}
# agy's reported plan_tier lags a subscription change. Set this to the real plan
# to override the badge; None means show whatever agy reports. Clear it once agy
# catches up, so this can't quietly outlive the next plan change.
AGY_PLAN = "Google AI Plus"  # agy still says "Google AI Pro" as of 2026-08-05
AGY_MAX_AGE = 600  # refresh the cached quota once it is this many seconds old
AGY_BACKOFF = 900  # after a failed refresh, wait this long before trying again

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
    if mins < 1440:
        return f"{mins // 60}h {mins % 60}m 전"
    return f"{mins // 1440}d {mins % 1440 // 60}h 전"


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
    oauth = creds.get("claudeAiOauth") or creds
    token = oauth.get("accessToken")
    # plan badge, e.g. subscriptionType "max" + rateLimitTier "..._5x" → "Max 5x"
    plan = (oauth.get("subscriptionType") or "").capitalize() or None
    tier_suffix = (oauth.get("rateLimitTier") or "").rsplit("_", 1)[-1]
    if plan and tier_suffix.endswith("x") and tier_suffix[:-1].isdigit():
        plan = f"{plan} {tier_suffix}"
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
    return {"provider": "Claude Code", "plan": plan, "updated": "방금", "rows": rows}


_CODEX_RPC_CACHE = {"at": 0.0, "value": None}
_CODEX_RPC_TTL = 60.0


def codex_rpc_rate_limits(timeout=12.0):
    """Live quota from the Codex app server, or None if it cannot be reached.

    `codex app-server` speaks JSON-RPC over stdio and exposes
    `account/rateLimits/read`, which queries the account directly. This is the
    only source that reflects MCP-driven usage: calls made through the
    `codex mcp-server` path write no rollout file under ~/.codex/sessions, so
    the snapshot scraper below cannot see them and silently reports whatever
    the last interactive CLI session happened to leave behind.

    Field names differ from the rollout format (camelCase, `windowDurationMins`
    rather than `window_minutes`), so the two paths are mapped separately.

    Results are cached for `_CODEX_RPC_TTL`. Unlike the file scrape this costs a
    process spawn and an account API round trip, and live mode refreshes as
    often as every 2 s, so an uncached call per frame would hammer the endpoint
    for data that changes far more slowly.
    """
    if (
        _CODEX_RPC_CACHE["value"] is not None
        and time.monotonic() - _CODEX_RPC_CACHE["at"] < _CODEX_RPC_TTL
    ):
        return _CODEX_RPC_CACHE["value"]

    proc = None
    try:
        proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except (OSError, ValueError):
        return None  # codex not installed or not on PATH

    lines = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(ln) for ln in proc.stdout], daemon=True
    ).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {
                "name": "usage-watch", "title": "usage-watch", "version": "0.1.0"}},
        })
        # The server emits unsolicited notifications (config warnings, remote
        # control status) interleaved with replies, so match on the id rather
        # than assuming the next line is ours.
        deadline = time.monotonic() + timeout
        sent_read = False
        while time.monotonic() < deadline:
            try:
                msg = json.loads(lines.get(timeout=0.2))
            except queue.Empty:
                # Bail out as soon as the server is gone rather than sitting on
                # the full timeout: a codex that is installed but cannot start
                # its app server (no login, older build) would otherwise stall
                # every refresh in live mode.
                if proc.poll() is not None and lines.empty():
                    return None
                continue
            except (json.JSONDecodeError, TypeError):
                continue
            if msg.get("id") == 1 and not sent_read:
                send({"jsonrpc": "2.0", "id": 2,
                      "method": "account/rateLimits/read", "params": {}})
                sent_read = True
            elif msg.get("id") == 2:
                limits = (msg.get("result") or {}).get("rateLimits")
                if limits:
                    _CODEX_RPC_CACHE.update(at=time.monotonic(), value=limits)
                return limits
        return None
    except (OSError, ValueError):
        return None
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def codex_card(now):
    live = codex_rpc_rate_limits()
    if live:
        rows = []
        for key in ("primary", "secondary"):
            w = live.get(key)
            if not w:
                continue
            try:
                mins = w.get("windowDurationMins") or (10080 if key == "primary" else 300)
                label = f"{mins // 1440}d" if mins >= 1440 else f"{mins // 60}h"
                resets = dt.datetime.fromtimestamp(w["resetsAt"], tz=KST)
                rows.append(make_row(label, w["usedPercent"], resets, now, mins / 60))
            except (KeyError, TypeError, ValueError, OSError, OverflowError):
                continue  # skip one malformed window, keep the rest
        if rows:
            return {
                "provider": "Codex",
                "plan": (live.get("planType") or "").capitalize() or None,
                "updated": "방금",
                "rows": rows,
            }

    # Fallback: scrape the newest interactive-CLI rollout. Older codex builds
    # have no app server, and the RPC needs a working login. This path can be
    # arbitrarily stale, which is why `updated` reports the snapshot's own age.
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


def _agy_bucket(key):
    """Bucket id → (row label, window hours), e.g. 'gemini-5h' → ('Gemini 5h', 5).

    Antigravity may add bucket ids without notice, so unrecognized suffixes fall
    back to parsing a trailing duration (e.g. '12h', '3d'); window hours of None
    means the period tick position is unknown rather than guessed.
    """
    prefix, _, suffix = key.rpartition("-")
    if not prefix:
        return key, None
    name = AGY_NAMES.get(prefix, prefix)
    window = AGY_WINDOWS.get(suffix)
    if window:
        return f"{name} {window[0]}", window[1]
    if len(suffix) > 1 and suffix[:-1].isdigit() and suffix[-1] in "hd":
        hours = int(suffix[:-1]) * (24 if suffix[-1] == "d" else 1)
        return f"{name} {suffix}", hours
    return f"{name} {suffix}", None


def agy_card(now):
    with open(AGY_CACHE) as fh:
        data = json.load(fh)
    quota = data.get("quota") or {}
    if not quota:
        raise RuntimeError("no quota cached yet — run `usage --refresh-agy`")

    rows = []
    # gemini buckets first, then third-party
    for key in sorted(quota, key=lambda k: (not k.startswith("gemini"), k)):
        b = quota[key]
        try:
            if b.get("disabled"):
                continue
            label, hours = _agy_bucket(key)
            row = make_row(label, 100.0 * (1.0 - b["remaining_fraction"]),
                           parse_iso(b["reset_time"]), now, hours or 168)
            if hours is None:
                row["period"] = 0.0  # unknown window: don't imply a tick position
            rows.append(row)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue  # skip one malformed bucket, keep the rest
    if not rows:
        raise RuntimeError(f"no usable quota buckets; keys={list(quota)}")

    # age of the quota numbers themselves — the file's mtime also moves when a
    # payload without quota merely carries the old values forward
    seen = data.get("quota_observed_at")
    age = (
        fmt_ago(dt.datetime.fromtimestamp(seen, tz=KST), now)
        if isinstance(seen, (int, float))
        else "시점 불명"
    )
    return {
        "provider": "Antigravity",
        "plan": AGY_PLAN or data.get("plan_tier"),
        "updated": age,
        "rows": rows,
    }


def refresh_agy(timeout=120):
    """Run agy headlessly until its status line reports quota, then stop it.

    agy only publishes quota to its status-line hook while running, and there is
    no API for it, so a short pty-hosted launch is how we refresh on demand.
    Costs no model tokens — quota arrives during start-up, before any prompt.
    """
    import fcntl
    import pty
    import re
    import select
    import struct
    import subprocess
    import termios

    cwd = os.path.expanduser("~")
    try:  # prefer a workspace agy already trusts, so it never prompts
        settings = json.load(open(os.path.expanduser("~/.gemini/antigravity-cli/settings.json")))
        trusted = [d for d in settings.get("trustedWorkspaces") or [] if os.path.isdir(d)]
        if trusted:
            cwd = trusted[0]
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    started_at = time.time()
    seen_before = _agy_quota_seen_at()
    grace = None  # once quota lands, linger briefly for the plan_tier payload
    master, slave = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
        try:
            proc = subprocess.Popen(["agy"], stdin=slave, stdout=slave, stderr=slave,
                                    cwd=cwd, env=dict(os.environ, TERM="xterm-256color"),
                                    close_fds=True, start_new_session=True)
        except (OSError, ValueError):
            return False  # agy not installed or not on PATH
        finally:
            os.close(slave)
            slave = None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.4)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                # answer terminal capability queries so the TUI proceeds to render
                text = chunk.decode("utf-8", "replace")
                for mode in re.findall(r"\x1b\[\?(\d+)\$p", text):
                    os.write(master, f"\x1b[?{mode};2$y".encode())
                if "\x1b[c" in text:
                    os.write(master, b"\x1b[?1;2c")
                if "\x1b[6n" in text:
                    os.write(master, b"\x1b[45;140R")

            seen = _agy_quota_seen_at()
            if seen > seen_before and seen >= started_at:
                # quota is fresh; plan_tier lands in a later payload, so linger
                if _agy_has_plan():
                    return True
                if grace is None:
                    grace = time.monotonic() + 8
                elif time.monotonic() > grace:
                    return True

            if proc.poll() is not None:  # agy exited (sign-in or trust prompt)
                break
        return False
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)
        if proc is not None and proc.poll() is None:
            _terminate_group(proc)


_agy_retry_after = 0.0  # monotonic deadline set after a failed refresh


def maybe_refresh_agy(max_age, timeout=45):
    """Refresh the Antigravity quota cache when it is missing or older than max_age.

    agy publishes quota only while it is running, so between agy sessions the
    Gemini rows would otherwise show hours-old numbers. Returns True if a
    refresh ran and produced fresh numbers. A failed refresh (agy absent or not
    signed in) backs off, so the live loop never stalls on every frame.
    """
    global _agy_retry_after
    if max_age <= 0:
        return False
    if time.time() - _agy_quota_seen_at() <= max_age:
        return False
    if time.monotonic() < _agy_retry_after:
        return False
    sys.stdout.write(fg(MUTED, "Antigravity 갱신 중…") + "\n")
    sys.stdout.flush()
    if refresh_agy(timeout=timeout):
        return True
    _agy_retry_after = time.monotonic() + AGY_BACKOFF
    return False


def _agy_quota_seen_at():
    """Epoch when the cached quota numbers were observed (0 if none)."""
    try:
        with open(AGY_CACHE) as fh:
            data = json.load(fh)
        seen = data.get("quota_observed_at")
        return seen if data.get("quota") and isinstance(seen, (int, float)) else 0
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _agy_has_plan():
    try:
        with open(AGY_CACHE) as fh:
            return bool(json.load(fh).get("plan_tier"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _terminate_group(proc):
    """Stop agy and any children it spawned (it runs its own language server)."""
    import signal
    import subprocess

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)  # reap, so no zombie is left behind
            return
        except subprocess.TimeoutExpired:
            continue


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


def render_card(card, label_w):
    lines = []
    left_plain = f"{card['provider']}  최신" + (f" {card['plan']}" if card.get("plan") else "")
    head = fg(TEXT, card["provider"], bold=True) + "  " + fg(GREEN, "최신")
    if card.get("plan"):
        head += " " + fg(BLUE, card["plan"])
    head += " " * max(1, 64 - disp_w(left_plain)) + fg(MUTED, f"{card['updated']} 갱신")
    lines.append(head)
    for r in card["rows"]:
        pct = fg(sev_color(r["usage"]), f"{r['usage']:>3}%", bold=True)
        reset = fg(MUTED, f"↻ {r['reset']}")
        lines.append(f"  {fg(TEXT, pad_to(r['label'], label_w))}{bar(r['usage'], r['period'])}  {pct}  {reset}")
    return "\n".join(lines)


def snapshot():
    now = dt.datetime.now(KST)
    cards, errors = [], []
    for fn in (claude_card, codex_card, agy_card):
        try:
            cards.append(fn(now))
        except Exception as e:  # keep the other card alive
            errors.append(f"{fn.__name__}: {e}")
    # one label column across every card, so all bars start at the same column
    label_w = max((disp_w(r["label"]) for c in cards for r in c["rows"]), default=0) + 2
    body = "\n\n".join(render_card(c, label_w) for c in cards)
    if errors:
        body += "\n" + fg(RED, "⚠ " + "; ".join(errors))
    stamp = fg(MUTED, now.strftime("%Y-%m-%d %H:%M:%S KST"))
    return f"{stamp}\n\n{body}\n"


def main():
    ap = argparse.ArgumentParser(description="Claude/Codex token-usage bars in the terminal")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("-n", "--interval", type=float, default=30, help="refresh seconds (default 30)")
    ap.add_argument("--refresh-agy", action="store_true",
                    help="briefly run agy to refresh its quota cache (~30 s, no tokens)")
    ap.add_argument("--agy-max-age", type=float, default=AGY_MAX_AGE, metavar="SEC",
                    help=f"auto-refresh the Antigravity quota when the cache is older"
                         f" than SEC (default {AGY_MAX_AGE:.0f}; 0 disables)")
    args = ap.parse_args()

    if args.refresh_agy:
        print("refreshing Antigravity quota…", flush=True)
        if not refresh_agy():
            print("  no quota reported; is agy signed in?")

    if args.once:
        maybe_refresh_agy(args.agy_max_age)
        print(snapshot())
        return
    try:
        sys.stdout.write("\033[?25l")  # hide cursor
        while True:
            maybe_refresh_agy(args.agy_max_age)
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
