"""Launch an Orca worker from a policy role and track run-local authorship.

Orca is the transport. This adapter attests the conductor identity from the current Orca
terminal, resolves the role through ``bindings.yaml``, reserves a serialized run-local
attempt, and then asks Orca to launch the selected agent/model/effort.

The state in ``tasks/orca/<run_id>/observed-author.json`` is separate from an engine task
contract: switching transports carries no authorship automatically. Producing and review
attempts are mutually exclusive within one Orca run. Their completion must be reconciled
with the exact task and dispatch before another tracked attempt can start.

Examples::

    orca_worker_start.py --role critic --task <id>
    orca_worker_start.py --settle reviewed --task <id> --dispatch-id <dispatch>
    orca_worker_start.py --role implementer --task <id>
    orca_worker_start.py --settle succeeded --task <id> --dispatch-id <dispatch>
    orca_worker_start.py --settle retained-output --task <id> --dispatch-id <dispatch>
    orca_worker_start.py --settle no-output --task <id> --dispatch-id <dispatch> \
        --confirm-no-output
    orca_worker_start.py --settle conductor-edited --task <id>
    orca_worker_start.py --settle abandoned --task <id> --confirm-no-output   # died mid-launch

Anything after ``--`` passes to ``worker-start`` except flags owned by this adapter.
Orca workers keep the user's normal sandbox and tools; this adapter does not reproduce
the engine dispatcher's read-only or restricted-tool containment.

Locking uses ``fcntl.flock``, so this runs where ``orca-ide`` runs: Linux, including WSL.

Known residual: the adapter bounds its own ``worker-start`` call, but if Orca's runtime keeps
completing a mutation after that client died, the dispatch can appear after the attempt was
abandoned, and Orca offers no way to ask about a request whose id was never received.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy_engine import (  # noqa: E402
    KNOWN_FAMILIES,
    OBSERVED_AUTHOR_FILE,
    PRODUCING_ROLES,
    UnreadableAuthorRecord,
    load_policy,
    resolve_binding,
)

ORCA_AGENT = {"claude-code": "claude", "codex": "codex"}
AGENT_HOST = {value: key for key, value in ORCA_AGENT.items()}
CONDUCTOR_FAMILY = {"claude-code": "claude", "codex": "codex"}
REVIEW_ROLES = frozenset({"critic", "verifier"})
TRACKED_ROLES = PRODUCING_ROLES | REVIEW_ROLES
RESERVED_PASSTHROUGH = {
    "--agent", "--model", "--effort", "--terminal", "--task", "--worktree", "--json",
    "--retry-request",
}
TERMINAL_DISPATCH_STATES = frozenset({"completed", "failed", "stopped"})
#: A reservation younger than this cannot be abandoned. The adapter itself caps its
#: ``worker-start`` call at this many seconds (see ``invoke_start``), so an adapter-launched
#: start older than this has returned or been killed -- the bound is ours, not an
#: assumption about Orca. It does not cover a runtime-side mutation Orca keeps processing
#: after its client died; Orca offers no way to ask about a request whose id was never
#: received.
ABANDON_MIN_AGE_SECONDS = 600
#: Every Orca query is bounded. One of them runs under the run lock, and a hang there
#: would block every later adapter call on the run in flock, which has no timeout.
ORCA_QUERY_TIMEOUT_SECONDS = 60
#: Attempts with no recorded dispatch: reserved but unanswered, or answered unreadably.
UNRECORDED_STATES = frozenset({"starting", "outcome_unknown"})
#: A plain path component: no slashes, no leading dot. This is what keeps
#: ``tasks/orca/<run_id>`` inside the store without any further path checks.
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
STATE_VERSION = 2


class StateError(RuntimeError):
    """The Orca response or local authorship state cannot be trusted."""


def orca_cli() -> str:
    """Return the one Orca executable selected for this session."""
    configured = os.environ.get("ORCA_CLI_COMMAND")
    if configured:
        return configured
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    return "orca-ide" if sys.platform.startswith("linux") else "orca"


def fail(reason: str) -> int:
    """Print a structured refusal to stderr and return the policy exit status."""
    print(json.dumps({"allowed": False, "reason": reason}, indent=2), file=sys.stderr)
    return 2


def json_result(cli: str, arguments: list[str], label: str) -> dict:
    """Run an Orca JSON query and require an object-shaped successful result."""
    try:
        completed = subprocess.run(
            [cli, *arguments, "--json"], capture_output=True, text=True, check=True,
            timeout=ORCA_QUERY_TIMEOUT_SECONDS,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise StateError(f"{label} failed: {error}") from error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise StateError(f"{label} returned an unsuccessful or malformed response")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise StateError(f"{label} returned no object result")
    return result


def run_id_for_task(cli: str, task_id: str) -> str:
    """Return the validated Orca run that owns ``task_id``."""
    result = json_result(cli, ["orchestration", "task-list"], "task-list")
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        raise StateError("task-list result has no task array")
    for task in tasks:
        if not isinstance(task, dict):
            raise StateError("task-list contains a non-object task")
        if task.get("id") != task_id:
            continue
        run_id = task.get("run_id")
        if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
            raise StateError(f"task {task_id} reports an unusable run id {run_id!r}")
        return run_id
    raise StateError(f"Orca task {task_id!r} not found in task-list")


def conductor_host(cli: str, asserted: str | None) -> str:
    """Read the current terminal's agent family from Orca; refuse a contradicting assertion.

    This closes the bad-default hole (a Codex conductor silently getting a Codex
    reviewer), not spoofing: the terminal queried is whichever ``ORCA_TERMINAL_HANDLE``
    names, and Orca's answer is taken as given.
    """
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        raise StateError("ORCA_TERMINAL_HANDLE is absent; conductor identity is ambiguous")
    result = json_result(cli, ["terminal", "show", "--terminal", handle], "terminal show")
    terminal = result.get("terminal")
    identity = terminal.get("agentIdentity") if isinstance(terminal, dict) else None
    detected = AGENT_HOST.get(identity)
    if detected is None:
        raise StateError(f"Orca does not report this terminal as Claude or Codex: {identity!r}")
    if asserted is not None and asserted != detected:
        raise StateError(
            f"--conductor-host {asserted!r} contradicts Orca's reported identity {detected!r}"
        )
    return detected


def store_paths(root: Path, run_id: str) -> tuple[Path, Path]:
    """Return the run's record directory and its persistent lock file."""
    store = root / "tasks" / "orca"
    (store / ".locks").mkdir(parents=True, exist_ok=True)
    return store / run_id, store / ".locks" / f"{run_id}.lock"


@contextmanager
def run_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive per-run lock. The kernel releases it if the holder dies."""
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def read_record(record_dir: Path) -> dict | None:
    """Read and validate the run's authorship state.

    A settled author (``family``/``source``) plus at most one ``active`` attempt. Nothing
    changes the settled fields while an attempt is active -- settlement clears the attempt
    first and a conductor edit is refused meanwhile -- so releasing an attempt is just
    dropping it; no rollback snapshot is kept.
    """
    path = record_dir / OBSERVED_AUTHOR_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UnreadableAuthorRecord(f"{path} cannot be read: {error}") from error
    if not isinstance(payload, dict):
        raise UnreadableAuthorRecord(f"{path} must contain an object")
    if payload.get("schema_version") != STATE_VERSION:
        raise UnreadableAuthorRecord(
            f"{path} uses unsupported state version {payload.get('schema_version')!r}"
        )
    family = payload.get("family")
    active = payload.get("active")
    if family is not None and family not in KNOWN_FAMILIES:
        raise UnreadableAuthorRecord(f"{path} records an unknown family: {family!r}")
    if active is not None:
        if not isinstance(active, dict):
            raise UnreadableAuthorRecord(f"{path} active attempt must be an object or null")
        required = ("attempt", "kind", "role", "family", "task_id", "start_state")
        if any(not isinstance(active.get(key), str) for key in required):
            raise UnreadableAuthorRecord(f"{path} active attempt is missing required string fields")
        if active["kind"] not in {"producer", "review"} or active["family"] not in KNOWN_FAMILIES:
            raise UnreadableAuthorRecord(f"{path} active attempt has invalid kind or family")
        for key in ("dispatch_id", "request_id"):
            if active.get(key) is not None and not isinstance(active[key], str):
                raise UnreadableAuthorRecord(f"{path} active {key} must be a string or null")
        start_args = active.get("worker_start_args")
        if not isinstance(start_args, list) or not all(isinstance(item, str) for item in start_args):
            raise UnreadableAuthorRecord(f"{path} active worker_start_args must be a string array")
        prior = active.get("prior_dispatch_id")
        if prior is not None and not isinstance(prior, str):
            raise UnreadableAuthorRecord(f"{path} active prior_dispatch_id must be a string or null")
        if not isinstance(active.get("reserved_at"), (int, float)):
            raise UnreadableAuthorRecord(f"{path} active reserved_at must be a number")
    if family is None and (active is None or active["kind"] != "producer"):
        raise UnreadableAuthorRecord(f"{path} has no settled author and no active producer")
    return payload


def write_record(record_dir: Path, payload: dict | None) -> None:
    """Atomically replace the state, or remove it when ``payload`` is ``None``."""
    path = record_dir / OBSERVED_AUTHOR_FILE
    if payload is None:
        path.unlink(missing_ok=True)
        return
    record_dir.mkdir(parents=True, exist_ok=True)
    descriptor, scratch_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=record_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
        os.replace(scratch_name, path)
    finally:
        Path(scratch_name).unlink(missing_ok=True)


def released(record: dict) -> dict | None:
    """The record with its attempt dropped; ``None`` when nothing settled remains."""
    if record.get("family") is None:
        return None
    return {**record, "active": None}


def receipt_metadata(stdout: str) -> tuple[bool, bool, str | None, str | None]:
    """Return acceptance, definitive rejection, dispatch ID, and request ID.

    Acceptance and rejection are **not** complements. A receipt Orca never produced --
    a killed process, a truncated pipe -- is neither: a worker may be live, so the
    reservation must stand. A well-formed ``ok: false`` naming no dispatch is a decision
    that nothing was created, and holding a reservation for it strands the run forever.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False, False, None, None
    if not isinstance(payload, dict):
        return False, False, None, None
    result = payload.get("result")
    # Documented top-level receipt fields, indexed rather than searched: a recursive hunt
    # finds whichever `dispatchId` comes first in dict order, which can be a retried
    # request's stale one -- and a stale id still passes the exact-match check later.
    dispatch_id = result.get("dispatchId") if isinstance(result, dict) else None
    request_id = result.get("requestId") if isinstance(result, dict) else None
    valid_dispatch = isinstance(dispatch_id, str) and bool(SAFE_ID.fullmatch(dispatch_id))
    accepted = payload.get("ok") is True and valid_dispatch
    # Orca's refusal contract, observed live for both a bad flag and a non-ready task:
    # {"ok": false, "error": {"code": ..., "message": ..., "data": ...}}. Anything else
    # that is not accepted -- ok:false with no error object, ok:true with no dispatch --
    # is a response we cannot interpret, and an uninterpretable response holds the
    # reservation, because a worker may exist.
    error = payload.get("error")
    rejected = (
        payload.get("ok") is False
        and dispatch_id is None
        and isinstance(error, dict)
        and isinstance(error.get("code"), str)
    )
    return accepted, rejected, dispatch_id, request_id


def current_dispatch(cli: str, task_id: str) -> str | None:
    """The dispatch id Orca currently reports for ``task_id``, or ``None`` for none.

    Only Orca's own well-formed answers count: ``ok: true`` with ``result.dispatch`` either
    ``null`` or an object carrying a valid ``id``. Anything else -- an unreachable CLI, a
    refusal, a result with no ``dispatch`` key, a dispatch of the wrong shape -- raises,
    because "could not tell" must never read as "none exists".
    """
    try:
        completed = subprocess.run(
            [cli, "orchestration", "dispatch-show", "--task", task_id, "--json"],
            capture_output=True, text=True, check=False, timeout=ORCA_QUERY_TIMEOUT_SECONDS,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise StateError(f"dispatch-show failed: {error}") from error
    if not isinstance(payload, dict):
        raise StateError("dispatch-show returned an uninterpretable response")
    result = payload.get("result")
    if payload.get("ok") is not True or not isinstance(result, dict) or "dispatch" not in result:
        raise StateError("dispatch-show returned an uninterpretable response")
    dispatch = result["dispatch"]
    if dispatch is None:
        return None
    dispatch_id = dispatch.get("id") if isinstance(dispatch, dict) else None
    if not isinstance(dispatch_id, str) or not SAFE_ID.fullmatch(dispatch_id):
        raise StateError("dispatch-show returned a dispatch without a usable id")
    return dispatch_id


def dispatch_status(cli: str, task_id: str, dispatch_id: str) -> str:
    """Verify an exact task/dispatch relationship and return its status."""
    result = json_result(
        cli, ["orchestration", "dispatch-show", "--task", task_id], "dispatch-show"
    )
    dispatch = result.get("dispatch")
    if not isinstance(dispatch, dict) or dispatch.get("id") != dispatch_id:
        raise StateError(f"task {task_id} is not bound to dispatch {dispatch_id}")
    status = dispatch.get("status")
    if not isinstance(status, str):
        raise StateError("dispatch-show returned no dispatch status")
    return status


def proposed_settlement(
    record: dict | None,
    outcome: str,
    task_id: str,
    dispatch_id: str | None,
    conductor_family: str,
    dispatch_state: str | None,
    confirm_no_output: bool,
    live_dispatch: str | None = None,
    now: float | None = None,
) -> dict | None:
    """Validate a settlement and return the replacement state.

    ``live_dispatch`` is the dispatch Orca reports for the task *now*, read under the
    same lock as the record; ``now`` is the clock used for the abandon age check.
    """
    active = record.get("active") if record else None
    if outcome == "conductor-edited":
        if active is not None:
            raise StateError("a conductor edit cannot clear an active worker; settle it first")
        return {
            "schema_version": STATE_VERSION,
            "family": conductor_family,
            "source": f"attested {conductor_family} conductor edit after task {task_id}",
            "active": None,
        }
    if active is None:
        raise StateError(f"nothing active for this run; cannot settle {outcome!r}")
    if active["task_id"] != task_id:
        raise StateError(
            f"active attempt belongs to task {active['task_id']!r}, not {task_id!r}"
        )
    prior = active.get("prior_dispatch_id")
    if outcome == "abandoned":
        # An attempt with no recorded dispatch never got a readable answer from Orca. It
        # may be dead, or its worker-start may still be running. Abandon only when both
        # hold: Orca reports the same dispatch the task had before this reservation
        # (nothing new was created), and the reservation is older than the adapter's own
        # launch cap. Drop the attempt and keep the settled author -- deleting the record
        # would hand the next critic to the conductor's family instead of the producer's.
        if active["start_state"] not in UNRECORDED_STATES:
            raise StateError("only an attempt with no recorded dispatch can be abandoned; a launched one settles by its dispatch")
        if not confirm_no_output:
            raise StateError("--settle abandoned requires --confirm-no-output after confirming the launching process is dead")
        if live_dispatch != prior:
            raise StateError(
                f"Orca now reports dispatch {live_dispatch!r} where the reservation saw {prior!r}: "
                "the launch created it, so settle this attempt with that --dispatch-id instead"
            )
        age = (now if now is not None else time.time()) - float(active.get("reserved_at", 0))
        if age < ABANDON_MIN_AGE_SECONDS:
            raise StateError(
                f"reservation is {age:.0f}s old; a worker-start may still be running. Abandon is "
                f"allowed after {ABANDON_MIN_AGE_SECONDS}s, when any bounded launch has finished"
            )
        return released(record)
    if dispatch_id is None:
        raise StateError("--dispatch-id is required to settle an active attempt")
    recorded_dispatch = active.get("dispatch_id")
    if recorded_dispatch is None:
        # No dispatch was ever recorded -- the launch is unanswered, or answered
        # unreadably. The only dispatch that can be this attempt's is the one Orca
        # reports as the task's CURRENT dispatch, read under this same lock, and only if
        # it is not the baseline the reservation saw. "Differs from the baseline" alone is
        # not enough: a dispatch two attempts old differs from it too, and a settler that
        # cached that id before the lock would clear a live attempt with it.
        if dispatch_id != live_dispatch:
            raise StateError(
                f"dispatch {dispatch_id} is not the task's current dispatch ({live_dispatch!r}); "
                "an attempt with no recorded dispatch can be settled only by the dispatch Orca "
                "reports now"
            )
        if dispatch_id == prior:
            raise StateError(
                f"dispatch {dispatch_id} already existed when this attempt was reserved, so it "
                "is not this attempt's. If nothing new appeared and the reservation is old, "
                "use --settle abandoned --confirm-no-output"
            )
        recorded_dispatch = dispatch_id
    if recorded_dispatch != dispatch_id:
        raise StateError(
            f"--dispatch-id {dispatch_id!r} contradicts active dispatch {recorded_dispatch!r}"
        )
    if dispatch_state not in TERMINAL_DISPATCH_STATES:
        raise StateError(f"dispatch {dispatch_id} is still {dispatch_state!r}; do not settle it")
    if outcome == "reviewed":
        if active["kind"] != "review":
            raise StateError("--settle reviewed requires an active critic or verifier")
        return released(record)
    if active["kind"] != "producer":
        raise StateError(f"--settle {outcome} requires an active producing worker")
    if outcome == "succeeded" and dispatch_state != "completed":
        raise StateError("--settle succeeded requires a completed dispatch")
    if outcome in {"succeeded", "retained-output"}:
        return {
            "schema_version": STATE_VERSION,
            "family": active["family"],
            "source": f"{active['role']} output from task {task_id}, dispatch {dispatch_id}",
            "active": None,
        }
    if not confirm_no_output:
        raise StateError("--settle no-output requires --confirm-no-output after checking the worktree")
    return released(record)


def settle(
    cli: str,
    record_dir: Path,
    lock_path: Path,
    outcome: str,
    task_id: str,
    dispatch_id: str | None,
    conductor_family: str,
    confirm_no_output: bool,
    dry_run: bool,
) -> int:
    """Reconcile one exact completed attempt, or record an attested conductor edit."""
    try:
        if outcome not in {"abandoned", "conductor-edited"} and dispatch_id is None:
            return fail("--dispatch-id is required to settle an active attempt")
        with run_lock(lock_path):
            # Orca is queried under the lock, bounded by ORCA_QUERY_TIMEOUT_SECONDS: a status
            # read before the lock can outlive whole attempts, and stale evidence must not
            # be applied to a record it was not read against.
            current = read_record(record_dir)
            state, live = None, None
            if outcome != "conductor-edited":
                live = current_dispatch(cli, task_id)
            if outcome not in {"abandoned", "conductor-edited"}:
                state = dispatch_status(cli, task_id, dispatch_id)
            replacement = proposed_settlement(
                current, outcome, task_id, dispatch_id, conductor_family, state,
                confirm_no_output, live_dispatch=live,
            )
            if not dry_run:
                write_record(record_dir, replacement)
    except (StateError, UnreadableAuthorRecord, OSError) as error:
        return fail(str(error))
    print(json.dumps({"settled": outcome, "dry_run": dry_run, "record": replacement}, indent=2))
    return 0


def invoke_start(command: list[str]) -> tuple[int, bool, bool, str | None, str | None]:
    """Run ``worker-start`` once, passing its output through to the caller.

    A process that cannot even be spawned is reported as a definitive rejection: nothing
    was created, so the reservation can be released like any other refusal.
    """
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=ABANDON_MIN_AGE_SECONDS
        )
    except OSError as error:
        print(f"worker-start could not execute: {error}", file=sys.stderr)
        return 1, False, True, None, None
    except subprocess.TimeoutExpired:
        # The launch may have gone through on Orca's side; we simply never saw the answer.
        print(f"worker-start exceeded {ABANDON_MIN_AGE_SECONDS}s and was killed; outcome unknown", file=sys.stderr)
        return 1, False, False, None, None
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    accepted, rejected, dispatch_id, request_id = receipt_metadata(completed.stdout)
    return completed.returncode, accepted, rejected, dispatch_id, request_id


def main(argv: list[str] | None = None) -> int:
    """Resolve a role, reserve its attempt, and invoke Orca's supervised worker start."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--role")
    parser.add_argument(
        "--settle",
        choices=("reviewed", "succeeded", "retained-output", "no-output", "conductor-edited", "abandoned"),
    )
    parser.add_argument("--resume-start", action="store_true")
    parser.add_argument("--task", required=True, help="Orca task id from task-create")
    parser.add_argument("--dispatch-id", help="exact dispatch from the worker-start receipt")
    parser.add_argument("--confirm-no-output", action="store_true")
    parser.add_argument("--author-family", choices=sorted(KNOWN_FAMILIES),
                        help="optional cross-check; refused when it contradicts the record")
    parser.add_argument("--required-family", choices=sorted(KNOWN_FAMILIES))
    parser.add_argument("--conductor-host", choices=sorted(CONDUCTOR_FAMILY))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--worktree", default="current")
    parser.add_argument("--run", help="Orca run id; verified against the task's actual run")
    parser.add_argument("--dry-run", action="store_true")
    args, passthrough = parser.parse_known_args(argv)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    if sum((bool(args.role), bool(args.settle), args.resume_start)) != 1:
        return fail("give exactly one of --role, --settle, or --resume-start")
    reserved = sorted(RESERVED_PASSTHROUGH & {token.split("=", 1)[0] for token in passthrough})
    if reserved:
        return fail(f"passthrough may not set {reserved}; those are decided by the policy")

    cli = orca_cli()
    if shutil.which(cli) is None:
        return fail(f"{cli} is not on PATH")
    try:
        run_id = run_id_for_task(cli, args.task)
        if args.run is not None and args.run != run_id:
            raise StateError(f"--run {args.run!r} but task {args.task} belongs to run {run_id!r}")
        record_dir, lock_path = store_paths(args.root, run_id)
        host = conductor_host(cli, args.conductor_host)
    except (StateError, OSError) as error:
        return fail(str(error))
    conductor_family = CONDUCTOR_FAMILY[host]

    if args.settle:
        return settle(
            cli, record_dir, lock_path, args.settle, args.task, args.dispatch_id,
            conductor_family, args.confirm_no_output, args.dry_run,
        )

    if args.resume_start:
        if args.dry_run:
            return fail("--resume-start has no dry-run; inspect the saved request first")
        try:
            with run_lock(lock_path):
                record = read_record(record_dir)
                active = record.get("active") if record else None
                if not active or active.get("task_id") != args.task:
                    raise StateError("no active start for this task")
                if active.get("start_state") != "outcome_unknown" or not active.get("request_id"):
                    raise StateError("the active attempt has no resumable Orca request identity")
                command = [
                    cli, *active["worker_start_args"], "--retry-request", active["request_id"], "--json"
                ]
                attempt = active["attempt"]
        except (StateError, UnreadableAuthorRecord, OSError, KeyError) as error:
            return fail(str(error))
    else:
        if args.role == "conductor":
            return fail("the conductor is the current session and may not be launched as a worker")
        author = args.author_family
        try:
            with run_lock(lock_path):
                record = read_record(record_dir)
                # Under the lock on purpose: a snapshot taken before it can predate another
                # attempt's entire lifetime, and that attempt's dispatch then looks "new"
                # to the reconciliation check and settles this reservation. One Orca read
                # bounded by ORCA_QUERY_TIMEOUT_SECONDS while holding the run is the price
                # of a baseline nobody can move between reading it and writing it down.
                prior_dispatch = current_dispatch(cli, args.task) if args.role in TRACKED_ROLES else None
                if args.role in REVIEW_ROLES:
                    observed = record.get("family") if record else conductor_family
                    if observed is None:
                        raise StateError("no settled author exists while a producer is active")
                    if author is not None and author != observed:
                        raise StateError(
                            f"--author-family {author!r} contradicts observed author {observed!r}"
                        )
                    author = observed
                bundle = load_policy(args.root)
                decision = resolve_binding(
                    bundle, args.role, author_family=author,
                    required_family=args.required_family, conductor_host=host,
                )
                if not decision.allowed or decision.details is None:
                    print(json.dumps(decision.as_dict(), indent=2), file=sys.stderr)
                    return 2
                backend = decision.details
                agent = ORCA_AGENT.get(backend["host"])
                if agent is None:
                    raise StateError(f"Orca has no agent for host {backend['host']!r}")
                worker_start_args = [
                    "orchestration", "worker-start", "--task", args.task,
                    "--worktree", args.worktree, "--agent", agent,
                    "--model", str(backend["model"]), "--effort", str(backend["effort"]),
                    *passthrough,
                ]
                command = [cli, *worker_start_args, "--json"]
                resolved = {
                    "role": args.role, "backend": backend["backend"],
                    "family": backend["family"], "model": backend["model"],
                    "effort": backend["effort"], "author_family": author,
                    "conductor_host": host, "run": run_id,
                }
                # Before the dry-run return, so the preview refuses exactly where a launch would.
                if args.role in TRACKED_ROLES and record and record.get("active"):
                    active = record["active"]
                    raise StateError(
                        f"{active['kind']} attempt {active['task_id']} is already active for run {run_id}"
                    )
                if args.dry_run:
                    print(json.dumps({"resolved": resolved, "command": command}, indent=2))
                    return 0
                attempt = uuid.uuid4().hex
                if args.role in TRACKED_ROLES:
                    if args.role in REVIEW_ROLES and record is None:
                        record = {
                            "schema_version": STATE_VERSION,
                            "family": conductor_family,
                            "source": f"attested {conductor_family} conductor (no producer ran)",
                            "active": None,
                        }
                    active = {
                        "attempt": attempt,
                        "kind": "producer" if args.role in PRODUCING_ROLES else "review",
                        "role": args.role,
                        "family": backend["family"],
                        "task_id": args.task,
                        "dispatch_id": None,
                        "request_id": None,
                        "start_state": "starting",
                        "worker_start_args": worker_start_args,
                        "prior_dispatch_id": prior_dispatch,
                        "reserved_at": time.time(),
                    }
                    record = {
                        "schema_version": STATE_VERSION,
                        "family": record.get("family") if record else None,
                        "source": record.get("source") if record else None,
                        "active": active,
                    }
                    write_record(record_dir, record)
        except (StateError, UnreadableAuthorRecord, OSError) as error:
            return fail(str(error))
        print(json.dumps({"resolved": resolved}), file=sys.stderr)

    status, accepted, rejected, dispatch_id, request_id = invoke_start(command)
    if args.role not in TRACKED_ROLES and not args.resume_start:
        return status
    if rejected and args.resume_start:
        # The retry was refused; the original request may still have produced a worker.
        return fail(
            "the retry was rejected; the original attempt stays outcome_unknown because its "
            "worker may be live -- inspect dispatch-show before recovering by hand"
        )
    if rejected:
        # Orca decided nothing was created, so there is nothing to reconcile. Holding the
        # reservation would strand the run: resume needs a request id it never issued,
        # settle needs a dispatch that does not exist, and a conductor edit is refused
        # while an attempt is active. Release it and report the refusal.
        try:
            with run_lock(lock_path):
                current = read_record(record_dir)
                if current and (current.get("active") or {}).get("attempt") == attempt:
                    write_record(record_dir, released(current))
        except (StateError, UnreadableAuthorRecord, OSError) as error:
            return fail(f"worker-start was rejected and the reservation could not be released: {error}")
        return fail("worker-start was rejected by Orca; nothing started and the reservation is released")
    try:
        with run_lock(lock_path):
            current = read_record(record_dir)
            active = current.get("active") if current else None
            if not active or active.get("attempt") != attempt:
                raise StateError("active attempt changed while worker-start was running")
            active = {
                **active,
                "dispatch_id": dispatch_id if accepted else active.get("dispatch_id"),
                "request_id": request_id or active.get("request_id"),
                "start_state": "started" if accepted else "outcome_unknown",
            }
            write_record(record_dir, {**current, "active": active})
    except (StateError, UnreadableAuthorRecord, OSError) as error:
        return fail(f"worker-start returned but its state could not be recorded: {error}")
    if not accepted:
        return fail(
            "worker-start outcome is unknown; the run remains locked. Inspect the Orca request/"
            "dispatch and use --resume-start when a saved request identity is available"
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
