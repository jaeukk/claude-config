#!/usr/bin/env python3
"""Antigravity CLI status-line hook: render a compact line and cache the quota.

Antigravity pipes its agent-state JSON to this script on stdin whenever state
changes. Antigravity's 5h/weekly quota has no standalone API, so we keep the
quota portion here for `usage-watch.py` to read, then print a short status line
so the agy status bar still shows something useful.

Only quota/plan fields are cached — the raw payload also carries the account
email and transcript paths, which this tool has no reason to persist.
"""
import json
import os
import sys
import tempfile
import time

CACHE = os.path.expanduser("~/.cache/agy-usage.json")


def _prev():
    try:
        with open(CACHE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _cache(data):
    """Persist quota + plan, carrying values forward across payloads that omit them.

    `quota` and `plan_tier` are omitempty and do not arrive in the same payload,
    so each is carried over. quota_observed_at records when the quota numbers
    themselves were seen — readers must not infer freshness from file mtime,
    which also moves on carry-forward-only writes.
    """
    prev = _prev()
    quota = data.get("quota")
    quota = quota if isinstance(quota, dict) and quota else None

    snap = {}
    for key in ("product", "version", "plan_tier"):
        value = data.get(key) or prev.get(key)
        if value:
            snap[key] = value
    if quota:
        snap["quota"] = quota
        snap["quota_observed_at"] = time.time()
    elif prev.get("quota"):
        snap["quota"] = prev["quota"]
        if prev.get("quota_observed_at"):
            snap["quota_observed_at"] = prev["quota_observed_at"]

    directory = os.path.dirname(CACHE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # unique temp name: concurrent agy sessions must not clobber each other
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".agy-usage.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(snap, fh)
        os.replace(tmp, CACHE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _render(data):
    parts = []
    model = data.get("model")
    if isinstance(model, dict) and model.get("display_name"):
        parts.append(model["display_name"])
    ctx = data.get("context_window")
    if isinstance(ctx, dict) and isinstance(ctx.get("used_percentage"), (int, float)):
        parts.append(f"ctx {ctx['used_percentage']:.0f}%")
    quota = data.get("quota")
    if isinstance(quota, dict):
        for key in ("gemini-5h", "gemini-weekly"):
            bucket = quota.get(key)
            if isinstance(bucket, dict) and isinstance(
                bucket.get("remaining_fraction"), (int, float)
            ):
                label = "5h" if key.endswith("5h") else "7d"
                parts.append(f"{label} {100 * (1 - bucket['remaining_fraction']):.0f}%")
    vcs = data.get("vcs")
    if isinstance(vcs, dict) and vcs.get("branch"):
        parts.append(vcs["branch"] + ("*" if vcs.get("dirty") else ""))
    return " │ ".join(parts)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    # a hook that raises would surface as an error in the user's status bar
    try:
        _cache(data)
    except Exception:
        pass
    try:
        sys.stdout.write(_render(data))
    except Exception:
        pass


if __name__ == "__main__":
    main()
