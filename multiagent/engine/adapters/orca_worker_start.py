"""Start an Orca worker for a policy role, with model and effort taken from the tier map.

Orca's ``worker-start`` takes ``--agent/--model/--effort`` by hand and applies no policy.
This adapter resolves the role through ``bindings.yaml`` first -- family independence,
tier, pinned model, effort -- and then launches exactly that. Orca stays the transport;
the policy stays here.

Usage::

    orca_worker_start.py --role critic --task <orca_task_id> --author-family claude
    orca_worker_start.py --role runner --task <id> --worktree new-child --name shard-3
    orca_worker_start.py --role verifier --task <id> --author-family codex --dry-run

Anything after ``--`` is passed to ``worker-start`` untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy_engine import KNOWN_FAMILIES, load_policy, resolve_binding  # noqa: E402

#: Backend ``host`` -> Orca ``--agent`` value. ``agy`` is absent on purpose: Orca has no
#: Gemini agent, and the policy has it disabled (D14) regardless.
ORCA_AGENT = {"claude-code": "claude", "codex": "codex"}


def orca_cli() -> str:
    """Return the Orca executable for this session.

    Follows the skill's resolution order: ``ORCA_CLI_COMMAND`` if set, else ``orca-ide``
    on Linux (bare ``orca`` there is the GNOME screen reader), else ``orca``.
    """
    configured = os.environ.get("ORCA_CLI_COMMAND")
    if configured:
        return configured
    return "orca-ide" if sys.platform.startswith("linux") else "orca"


def main(argv: list[str] | None = None) -> int:
    """Resolve the role, then exec ``worker-start`` with the resolved launch flags."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", required=True)
    parser.add_argument("--task", required=True, help="Orca task id from task-create")
    parser.add_argument("--author-family", choices=sorted(KNOWN_FAMILIES),
                        help="who produced the artifact; required for critic and verifier")
    parser.add_argument("--required-family", choices=sorted(KNOWN_FAMILIES),
                        help="force a family, e.g. the fallback during a vendor outage")
    parser.add_argument("--conductor-host", default="claude-code")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--worktree", default="current")
    parser.add_argument("--dry-run", action="store_true")
    args, passthrough = parser.parse_known_args(argv)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    bundle = load_policy(args.root)
    decision = resolve_binding(
        bundle, args.role,
        author_family=args.author_family,
        required_family=args.required_family,
        conductor_host=args.conductor_host,
    )
    if not decision.allowed or decision.details is None:
        print(json.dumps(decision.as_dict(), indent=2), file=sys.stderr)
        return 2
    backend = decision.details
    agent = ORCA_AGENT.get(backend["host"])
    if agent is None:
        print(json.dumps({"allowed": False, "reason": f"Orca has no agent for host {backend['host']!r}"}),
              file=sys.stderr)
        return 2

    command = [
        orca_cli(), "orchestration", "worker-start",
        "--task", args.task, "--worktree", args.worktree,
        "--agent", agent, "--model", str(backend["model"]), "--effort", str(backend["effort"]),
        *passthrough, "--json",
    ]
    resolved = {"role": args.role, "backend": backend["backend"], "family": backend["family"],
                "model": backend["model"], "effort": backend["effort"]}
    if args.dry_run:
        print(json.dumps({"resolved": resolved, "command": command}, indent=2))
        return 0
    if shutil.which(command[0]) is None:
        print(f"{command[0]} is not on PATH", file=sys.stderr)
        return 2
    # Provenance goes to stderr so stdout stays Orca's single JSON document.
    print(json.dumps({"resolved": resolved}), file=sys.stderr)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
