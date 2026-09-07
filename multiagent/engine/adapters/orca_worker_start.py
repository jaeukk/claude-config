"""Start an Orca worker for a policy role, with model and effort taken from the tier map.

Orca's ``worker-start`` takes ``--agent/--model/--effort`` by hand and applies no policy.
This adapter resolves the role through ``bindings.yaml`` first -- family independence,
tier, pinned model, effort -- and then launches exactly that. Orca stays the transport;
the policy stays here.

Authorship is tracked per Orca run in ``tasks/orca/<run_id>/observed-author.json`` -- the
same sidecar the engine and the hook use, so ``critic`` and ``verifier`` launches take the
author family from the record instead of from whatever the coordinator types.

Usage::

    orca_worker_start.py --role implementer --task <orca_task_id>       # records author
    orca_worker_start.py --role critic --task <orca_task_id>            # reads it back
    orca_worker_start.py --role runner --task <id> --worktree new-child -- --name shard-3

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
from policy_engine import (  # noqa: E402
    KNOWN_FAMILIES,
    OBSERVED_AUTHOR_FILE,
    PRODUCING_ROLES,
    UnreadableAuthorRecord,
    load_policy,
    observed_author_family,
    resolve_binding,
)

#: Backend ``host`` -> Orca ``--agent`` value. ``agy`` is absent on purpose: Orca has no
#: Gemini agent, and the policy has it disabled (D14) regardless.
ORCA_AGENT = {"claude-code": "claude", "codex": "codex"}
REVIEW_ROLES = frozenset({"critic", "verifier"})


def orca_cli() -> str:
    """Return the Orca executable for this session.

    Follows the skill's resolution order: ``ORCA_CLI_COMMAND`` if set, else ``orca-ide``
    on Linux (bare ``orca`` there is the GNOME screen reader), else ``orca``.
    """
    configured = os.environ.get("ORCA_CLI_COMMAND")
    if configured:
        return configured
    return "orca-ide" if sys.platform.startswith("linux") else "orca"


def run_id_for_task(cli: str, task_id: str) -> str:
    """Return the Orca run that owns ``task_id``, via ``task-list``."""
    listing = subprocess.run(
        [cli, "orchestration", "task-list", "--json"], capture_output=True, text=True, check=True
    )
    for task in json.loads(listing.stdout).get("result", {}).get("tasks", []):
        if task.get("id") == task_id:
            return str(task["run_id"])
    raise SystemExit(f"Orca task {task_id!r} not found in task-list")


def fail(reason: str) -> int:
    """Print a structured refusal to stderr and return the policy exit status."""
    print(json.dumps({"allowed": False, "reason": reason}, indent=2), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Resolve the role, then exec ``worker-start`` with the resolved launch flags."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", required=True)
    parser.add_argument("--task", required=True, help="Orca task id from task-create")
    parser.add_argument("--author-family", choices=sorted(KNOWN_FAMILIES),
                        help="who produced the artifact; must agree with the run's record when one exists")
    parser.add_argument("--required-family", choices=sorted(KNOWN_FAMILIES),
                        help="force a family, e.g. the fallback during a vendor outage")
    parser.add_argument("--conductor-host", default="claude-code")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--worktree", default="current")
    parser.add_argument("--run", help="Orca run id; looked up from --task when omitted")
    parser.add_argument("--dry-run", action="store_true")
    args, passthrough = parser.parse_known_args(argv)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    cli = orca_cli()
    run_id = args.run or run_id_for_task(cli, args.task)
    record_dir = args.root / "tasks" / "orca" / run_id

    # Reviewers take the author from the run's record, not from the command line. A
    # declaration is accepted only when it agrees with the record or no record exists.
    author = args.author_family
    if args.role in REVIEW_ROLES:
        try:
            recorded = observed_author_family(record_dir)
        except UnreadableAuthorRecord as error:
            return fail(f"{args.role} blocked: {error}")
        if recorded is None and author is None:
            return fail(
                f"{args.role} blocked: no producing worker is recorded for run {run_id} and no "
                "--author-family was given; declare who authored the artifact"
            )
        if recorded is not None and author is not None and recorded != author:
            return fail(
                f"{args.role} blocked: --author-family {author!r} contradicts the recorded "
                f"producer {recorded!r} for run {run_id}"
            )
        author = recorded or author

    bundle = load_policy(args.root)
    decision = resolve_binding(
        bundle, args.role,
        author_family=author,
        required_family=args.required_family,
        conductor_host=args.conductor_host,
    )
    if not decision.allowed or decision.details is None:
        print(json.dumps(decision.as_dict(), indent=2), file=sys.stderr)
        return 2
    backend = decision.details
    agent = ORCA_AGENT.get(backend["host"])
    if agent is None:
        return fail(f"Orca has no agent for host {backend['host']!r}")

    command = [
        cli, "orchestration", "worker-start",
        "--task", args.task, "--worktree", args.worktree,
        "--agent", agent, "--model", str(backend["model"]), "--effort", str(backend["effort"]),
        *passthrough, "--json",
    ]
    resolved = {"role": args.role, "backend": backend["backend"], "family": backend["family"],
                "model": backend["model"], "effort": backend["effort"], "author_family": author,
                "run": run_id}
    if args.dry_run:
        print(json.dumps({"resolved": resolved, "command": command}, indent=2))
        return 0
    if shutil.which(command[0]) is None:
        return fail(f"{command[0]} is not on PATH")
    # Provenance goes to stderr so stdout stays Orca's single JSON document.
    print(json.dumps({"resolved": resolved}), file=sys.stderr)
    status = subprocess.run(command, check=False).returncode
    if status == 0 and args.role in PRODUCING_ROLES:
        # Recorded at launch, like the hook's native path: Orca's worker finishes later
        # via worker_done, so this is intent, not a completed run. A producer that fails
        # still leaves its family here -- the skill's "failed native producer" rule applies.
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / OBSERVED_AUTHOR_FILE).write_text(
            json.dumps({"family": backend["family"],
                        "source": f"{args.role} via orca worker-start (launch intent), task {args.task}"},
                       indent=2) + "\n",
            encoding="utf-8",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
