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

Local edits survive: Claude Code (/config, permission approvals) and Orca write
straight into the live settings.json. Before composing, every value that differs
from what the composer last wrote (settings.json.composed, untracked) is copied
into settings.machine-local.json. With no snapshot yet, the live file wins
everywhere it differs from the layers.
"""
import json
import platform
import sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parent.parent  # scripts/ -> ~/.claude
SNAPSHOT = CLAUDE / "settings.json.composed"  # what the composer last wrote


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


def local_edits(baseline: dict, live: dict, prefix: str = "") -> tuple[dict, list[str]]:
    """Find what was edited in the live settings since the composer wrote them.

    Parameters
    ----------
    baseline : dict
        Settings the composer last wrote (or would write now, on a first run).
    live : dict
        The current settings.json.
    prefix : str
        Dotted path of ``live`` inside the top-level document; used in messages.

    Returns
    -------
    patch : dict
        Overlay that, deep-merged over ``baseline``, reproduces every value of
        ``live``. Dicts recurse; lists and scalars are stored whole.
    removed : list of str
        Dotted paths present in ``baseline`` but deleted in ``live``. A merge
        overlay cannot express a deletion, so the caller only reports these.
    """
    patch: dict = {}
    removed: list[str] = []
    for key, value in live.items():
        old = baseline.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            sub, gone = local_edits(old, value, f"{prefix}{key}.")
            if sub:
                patch[key] = sub
            removed += gone
        elif key not in baseline or value != old:
            patch[key] = value
    removed += [f"{prefix}{key}" for key in baseline if key not in live]
    return patch, removed


def compose(layers: list[Path]) -> dict:
    """Deep-merge the existing layer files in order; later layers win."""
    composed: dict = {}
    for p in layers:
        if p.exists():
            composed = deep_merge(composed, json.loads(p.read_text(encoding="utf-8")))
    return composed


def main() -> int:
    machine = sys.argv[1] if len(sys.argv) > 1 else detect_machine()
    local_layer = CLAUDE / "settings.machine-local.json"
    layers = [
        CLAUDE / "settings.base.json",
        CLAUDE / f"settings.machine.{machine}.json",
        local_layer,
    ]
    if not layers[0].exists():
        print(f"ERROR: missing {layers[0]}", file=sys.stderr)
        return 1
    composed = compose(layers)

    # Absorb local edits of the live file into the untracked layer before overwriting it.
    target = CLAUDE / "settings.json"
    if target.exists():
        live = json.loads(target.read_text(encoding="utf-8"))
        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else composed
        patch, removed = local_edits(baseline, live)
        for path in removed:
            print(f"WARNING: {path} was deleted from settings.json but comes back from a layer; "
                  "delete it from its layer to drop it", file=sys.stderr)
        if patch:
            local = json.loads(local_layer.read_text(encoding="utf-8")) if local_layer.exists() else {}
            if local_layer.exists():
                (CLAUDE / "settings.machine-local.json.bak").write_text(
                    local_layer.read_text(encoding="utf-8"), encoding="utf-8")
            local_layer.write_text(
                json.dumps(deep_merge(local, patch), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            print(f"kept local edits in {local_layer.name}: {', '.join(sorted(patch))}")
            composed = compose(layers)

    # Team account dir: the same layers plus settings.team.json (model override), written
    # as a real file. A symlink would let a /model save inside a team session land in the
    # shared settings.json and change the private default too.
    team_dir = Path.home() / ".claude-team"
    if team_dir.exists():
        team_layer = CLAUDE / "settings.team.json"
        team = composed
        if team_layer.exists():
            team = deep_merge(composed, json.loads(team_layer.read_text(encoding="utf-8")))
        team_target = team_dir / "settings.json"
        team_text = json.dumps(team, indent=2, ensure_ascii=False) + "\n"
        if team_target.is_symlink():
            team_target.unlink()
        if not team_target.exists() or team_target.read_text(encoding="utf-8") != team_text:
            team_target.write_text(team_text, encoding="utf-8")
            print(f"composed {team_target} (team layer: {team_layer.exists()})")

    new_text = json.dumps(composed, indent=2, ensure_ascii=False) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == new_text:
        print(f"settings.json already up to date ({machine})")
    else:
        if target.exists():
            (CLAUDE / "settings.json.bak").write_text(
                target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(new_text, encoding="utf-8")
        print(f"composed settings.json for machine={machine} from {sum(p.exists() for p in layers)} layer(s)")
    # Written last, so an interrupted run never records a snapshot of a file it did not write.
    SNAPSHOT.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
