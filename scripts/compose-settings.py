#!/usr/bin/env python3
"""Compose the live ~/.claude/settings.json from tracked fragments.

Layering (later wins, deep-merged):
  settings.base.json                 tracked, portable across machines
  settings.machine.<machine>.json    tracked, machine-specific (paths, statusLine)
  settings.machine-local.json        UNTRACKED, this machine's accumulated
                                     permission approvals and private overrides

Machine is auto-detected (wsl | windows) or passed as argv[1].
Run after every `git pull` in ~/.claude. Idempotent; backs up the previous
settings.json to settings.json.bak before overwriting.
"""
import json, platform, sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parent.parent  # scripts/ -> ~/.claude


def detect_machine() -> str:
    if platform.system() == "Windows":
        return "windows"
    if "microsoft" in platform.uname().release.lower():
        return "wsl"
    return "linux"


def deep_merge(a: dict, b: dict) -> dict:
    """Deep-merge b over a. Dicts merge recursively; everything else (incl. lists) is replaced."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def main() -> int:
    machine = sys.argv[1] if len(sys.argv) > 1 else detect_machine()
    layers = [
        CLAUDE / "settings.base.json",
        CLAUDE / f"settings.machine.{machine}.json",
        CLAUDE / "settings.machine-local.json",
    ]
    composed: dict = {}
    for p in layers:
        if p.exists():
            composed = deep_merge(composed, json.loads(p.read_text(encoding="utf-8")))
        elif "base" in p.name:
            print(f"ERROR: missing {p}", file=sys.stderr)
            return 1

    target = CLAUDE / "settings.json"
    new_text = json.dumps(composed, indent=2, ensure_ascii=False) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == new_text:
            print(f"settings.json already up to date ({machine})")
            return 0
        (CLAUDE / "settings.json.bak").write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(new_text, encoding="utf-8")
    print(f"composed settings.json for machine={machine} from {sum(p.exists() for p in layers)} layer(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
