"""Policy enforcement and host dispatch for the local multi-agent installation."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


POLICY_FILES = ("roles.yaml", "bindings.yaml", "backends.yaml", "routing.yaml", "approvals.yaml")
VALID_EFFORTS = {"low", "medium", "high"}
CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".m", ".php", ".py", ".rb", ".rs", ".scala", ".sh", ".swift", ".ts", ".tsx",
}


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """Loaded machine-readable policy documents.

    Attributes
    ----------
    root:
        Multi-agent installation root.
    documents:
        Documents keyed by filename without the extension.
    """

    root: Path
    documents: dict[str, dict[str, Any]]

    @property
    def roles(self) -> dict[str, Any]:
        """Return role definitions."""
        return self.documents["roles"]["roles"]

    @property
    def bindings(self) -> dict[str, Any]:
        """Return role-to-backend bindings."""
        return self.documents["bindings"]["bindings"]

    @property
    def backends(self) -> dict[str, Any]:
        """Return backend registry entries."""
        return self.documents["backends"]["backends"]


@dataclass(frozen=True, slots=True)
class Decision:
    """Authorization or validation decision."""

    allowed: bool
    reason: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload: dict[str, Any] = {"allowed": self.allowed, "reason": self.reason}
        if self.details is not None:
            payload["details"] = self.details
        return payload


def utc_now() -> str:
    """Return a UTC timestamp in RFC 3339 form."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_document(path: Path) -> dict[str, Any]:
    """Load a JSON-compatible YAML or JSON document.

    Parameters
    ----------
    path:
        Document path.

    Returns
    -------
    dict
        Parsed object.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def load_policy(root: Path) -> PolicyBundle:
    """Load all policy documents below ``root/policy``."""
    resolved = root.resolve()
    documents = {
        Path(filename).stem: load_document(resolved / "policy" / filename)
        for filename in POLICY_FILES
    }
    return PolicyBundle(root=resolved, documents=documents)


def validate_policy(bundle: PolicyBundle) -> tuple[list[str], list[str]]:
    """Validate cross-references and architecture invariants.

    Returns
    -------
    tuple[list[str], list[str]]
        Errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    backend_names = set(bundle.backends)

    if set(bundle.roles) != set(bundle.bindings):
        errors.append("roles and bindings must define the same role names")

    forbidden_alias = re.compile(r"(?:gpt|opus|haiku|sonnet|sol|terra|[0-9])", re.IGNORECASE)
    for alias, backend in bundle.backends.items():
        if forbidden_alias.search(alias):
            errors.append(f"backend alias is model-dependent: {alias}")
        required = {"host", "family", "model", "capabilities", "sandbox", "writes_mediated", "supports_subagents"}
        missing = sorted(required - set(backend))
        if missing:
            errors.append(f"backend {alias} lacks {', '.join(missing)}")

    for role, binding in bundle.bindings.items():
        candidates = binding.get("candidates", binding.get("pool", []))
        if not candidates:
            errors.append(f"role {role} has no backend candidates")
            continue
        for candidate in candidates:
            alias = candidate.get("backend")
            effort = candidate.get("effort")
            if alias not in backend_names:
                errors.append(f"role {role} references unknown backend {alias}")
                continue
            if effort not in VALID_EFFORTS:
                errors.append(f"role {role} has invalid effort {effort}")
            required_caps = set(bundle.roles[role].get("required_capabilities", []))
            actual_caps = set(bundle.backends[alias].get("capabilities", []))
            missing_caps = sorted(required_caps - actual_caps)
            if missing_caps:
                errors.append(f"backend {alias} cannot serve {role}; missing {missing_caps}")

    if bundle.bindings.get("conductor", {}).get("mode") != "session_assertion":
        errors.append("conductor must be a session assertion, not an engine-selected role")
    if any(item.get("effort") != "high" for item in bundle.bindings.get("implementer", {}).get("candidates", [])):
        errors.append("all implementer candidates must use high effort")
    critic = bundle.bindings.get("critic", {})
    if critic.get("mode") != "different_family_from_author":
        errors.append("critic must differ from the artifact author family")
    verifier = bundle.bindings.get("verifier", {})
    if verifier.get("mode") != "different_family_from_author":
        errors.append("verifier must differ from the artifact author family")
    bulk_families = {
        bundle.backends[item["backend"]]["family"]
        for item in bundle.bindings.get("bulk_worker", {}).get("pool", [])
        if item.get("backend") in bundle.backends
    }
    if bulk_families != {"claude", "codex"}:
        errors.append("bulk_worker pool must include Claude and Codex families")
    for alias, backend in bundle.backends.items():
        if backend.get("host") == "codex" and not backend.get("writes_mediated"):
            warnings.append(f"{alias} is intentionally read-only until mediated writes are validated")
    return errors, warnings


def resolve_binding(
    bundle: PolicyBundle,
    role: str,
    author_family: str | None = None,
    required_family: str | None = None,
) -> Decision:
    """Resolve a role to the first compatible backend.

    Parameters
    ----------
    bundle:
        Loaded policies.
    role:
        Model-independent role name.
    author_family:
        Family that produced the artifact, for independence checks.
    required_family:
        Optional host family constraint.
    """
    binding = bundle.bindings.get(role)
    if binding is None:
        return Decision(False, f"unknown role: {role}")
    candidates = binding.get("candidates", binding.get("pool", []))
    for candidate in candidates:
        backend = bundle.backends[candidate["backend"]]
        family = backend["family"]
        if required_family is not None and family != required_family:
            continue
        if binding.get("mode") == "different_family_from_author" and author_family == family:
            continue
        return Decision(
            True,
            "binding resolved",
            {"role": role, "backend": candidate["backend"], "effort": candidate["effort"], **backend},
        )
    return Decision(False, f"no compatible backend for {role}")


def validate_task(bundle: PolicyBundle, task: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Validate a task contract without third-party schema dependencies."""
    errors: list[str] = []
    warnings: list[str] = []
    required = {"schema_version", "task_id", "status", "conductor", "target_repo", "write_scope", "roles_plan", "approvals", "dispatch"}
    missing = sorted(required - set(task))
    if missing:
        errors.append(f"task lacks {', '.join(missing)}")
        return errors, warnings
    if task["schema_version"] != 1:
        errors.append("unsupported task schema version")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", str(task["task_id"])):
        errors.append("task_id must use lowercase letters, digits, dots, underscores, or hyphens")
    if task.get("status") not in {"pending", "active", "verifying", "complete", "blocked", "cancelled"}:
        errors.append("invalid task status")
    conductor = task.get("conductor", {})
    if conductor.get("host") != "claude-code" or conductor.get("backend") != "claude-frontier":
        errors.append("the active conductor must assert claude-code / claude-frontier")
    if not conductor.get("lease_owner"):
        errors.append("conductor.lease_owner is required")
    target = Path(str(task.get("target_repo", "")))
    if not target.is_absolute():
        errors.append("target_repo must be absolute")
    if not isinstance(task.get("write_scope"), list):
        errors.append("write_scope must be a list")
    unknown_roles = sorted(set(task.get("roles_plan", [])) - (set(bundle.roles) - {"conductor"}))
    if unknown_roles:
        errors.append(f"roles_plan contains unknown roles: {unknown_roles}")
    dispatch = task.get("dispatch", {})
    if not isinstance(dispatch.get("active_workers"), int) or dispatch.get("active_workers", -1) < 0:
        errors.append("dispatch.active_workers must be a non-negative integer")
    current_role = dispatch.get("current_role")
    if current_role is not None and current_role not in task.get("roles_plan", []):
        errors.append("dispatch.current_role must be null or planned")
    if not target.exists():
        warnings.append("target_repo does not currently exist")
    return errors, warnings


def _is_in_scope(path_value: str, target_repo: str, scopes: list[str]) -> bool:
    target = Path(target_repo).resolve()
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = target / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(target)
    except ValueError:
        return False
    for scope in scopes:
        scope_path = Path(scope)
        if not scope_path.is_absolute():
            scope_path = target / scope_path
        scope_path = scope_path.resolve(strict=False)
        if candidate == scope_path or scope_path in candidate.parents:
            return True
    return False


def authorize_action(bundle: PolicyBundle, task: dict[str, Any], action: dict[str, Any]) -> Decision:
    """Authorize a normalized orchestration action against a task contract."""
    task_errors, _ = validate_task(bundle, task)
    if task_errors:
        return Decision(False, f"invalid task contract: {task_errors[0]}")
    kind = action.get("kind")
    actor_role = action.get("actor_role", "conductor")
    approvals = set(task.get("approvals", {}).get("user", []))
    approval_policy = bundle.documents["approvals"]

    if kind == "spawn_worker":
        role = action.get("role")
        if actor_role != "conductor":
            return Decision(False, "recursive orchestration is forbidden")
        if role not in task["roles_plan"]:
            return Decision(False, f"worker role is not planned: {role}")
        max_fanout = bundle.documents["routing"]["defaults"]["max_fanout"]
        if task["dispatch"]["active_workers"] >= max_fanout:
            return Decision(False, "fan-out limit reached")
        author_family = task.get("author_family") if role in {"critic", "verifier"} else None
        return resolve_binding(bundle, role, author_family=author_family)

    if kind == "write":
        path_value = action.get("path")
        if not isinstance(path_value, str) or not path_value:
            return Decision(False, "write action requires a path")
        if actor_role not in {"conductor", "implementer"}:
            return Decision(False, f"role {actor_role} may not write")
        if actor_role == "implementer" and "implementer" not in task["roles_plan"]:
            return Decision(False, "implementer is not planned")
        if not _is_in_scope(path_value, task["target_repo"], task["write_scope"]):
            return Decision(False, "path is outside target_repo/write_scope")
        if actor_role == "conductor" and Path(path_value).suffix.lower() in CODE_SUFFIXES:
            maximum = approval_policy["direct_conductor_edit"]["max_code_files"]
            if task.get("direct_code_files", 0) >= maximum:
                return Decision(False, "conductor direct-code-edit limit reached")
        return Decision(True, "write is inside the approved task scope")

    if kind in {"conductor_handoff", "scope_expansion", "destructive_action", "external_side_effect", "secret_or_credential_access"}:
        if kind not in approvals:
            return Decision(False, f"user approval required: {kind}")
        return Decision(True, "recorded user approval found")

    return Decision(False, f"unknown action kind: {kind}")


def acquire_lease(task_dir: Path, owner: str, ttl_seconds: int = 300) -> Decision:
    """Acquire an exclusive task lease, replacing only an expired lease."""
    task_dir.mkdir(parents=True, exist_ok=True)
    lease_path = task_dir / "lease.json"
    now = time.time()
    payload = {
        "owner": owner,
        "acquired_at": utc_now(),
        "heartbeat_at": utc_now(),
        "expires_epoch": now + ttl_seconds,
    }
    encoded = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    for _ in range(2):
        try:
            descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            existing = load_document(lease_path)
            if float(existing.get("expires_epoch", 0)) > now:
                return Decision(False, f"task is leased by {existing.get('owner', 'unknown')}")
            stale = task_dir / f"lease.stale.{int(now)}.json"
            try:
                lease_path.replace(stale)
            except FileNotFoundError:
                continue
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        return Decision(True, "lease acquired", payload)
    return Decision(False, "lease acquisition raced with another conductor")


def heartbeat_lease(task_dir: Path, owner: str, ttl_seconds: int = 300) -> Decision:
    """Extend a lease owned by ``owner``."""
    lease_path = task_dir / "lease.json"
    if not lease_path.exists():
        return Decision(False, "task has no lease")
    payload = load_document(lease_path)
    if payload.get("owner") != owner:
        return Decision(False, "lease owner mismatch")
    payload["heartbeat_at"] = utc_now()
    payload["expires_epoch"] = time.time() + ttl_seconds
    lease_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return Decision(True, "lease heartbeat recorded", payload)


def release_lease(task_dir: Path, owner: str) -> Decision:
    """Release a lease owned by ``owner``."""
    lease_path = task_dir / "lease.json"
    if not lease_path.exists():
        return Decision(False, "task has no lease")
    payload = load_document(lease_path)
    if payload.get("owner") != owner:
        return Decision(False, "lease owner mismatch")
    lease_path.unlink()
    return Decision(True, "lease released")


def append_event(task_dir: Path, owner: str, event: dict[str, Any]) -> Decision:
    """Append an event only when the caller owns the live task lease."""
    lease_path = task_dir / "lease.json"
    if not lease_path.exists():
        return Decision(False, "task has no lease")
    lease = load_document(lease_path)
    if lease.get("owner") != owner or float(lease.get("expires_epoch", 0)) <= time.time():
        return Decision(False, "a live matching lease is required")
    record = {"at": utc_now(), "owner": owner, **event}
    with (task_dir / "events.ndjson").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return Decision(True, "event appended")


def build_codex_command(backend: dict[str, Any], host_mode: str, target_repo: Path) -> tuple[list[str], Path]:
    """Build a read-only native-Windows or WSL Codex command."""
    codex_args = [
        "exec", "--sandbox", "read-only", "--model", str(backend["model"]),
        "-c", f'model_reasoning_effort="{backend["effort"]}"',
        "--skip-git-repo-check", "--ephemeral", "--color", "never", "-",
    ]
    if host_mode == "native":
        executable = (
            shutil.which("codex.cmd") or shutil.which("codex")
            if os.name == "nt"
            else shutil.which("codex")
        )
        if executable is None:
            raise FileNotFoundError("codex executable is not on PATH")
        return [executable, *codex_args], target_repo
    if host_mode == "wsl":
        wsl = shutil.which("wsl.exe")
        if wsl is None:
            raise FileNotFoundError("wsl.exe is not on PATH")
        converted = subprocess.run(
            [wsl, "wslpath", "-a", str(target_repo)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return [wsl, "--cd", converted, "--exec", "codex", *codex_args], target_repo
    raise ValueError(f"unsupported Codex host mode: {host_mode}")


def dispatch_codex(
    bundle: PolicyBundle,
    task: dict[str, Any],
    role: str,
    brief_path: Path,
    host_mode: str,
    dry_run: bool,
) -> int:
    """Dispatch a bounded Codex worker with a read-only sandbox."""
    authorization = authorize_action(bundle, task, {"kind": "spawn_worker", "actor_role": "conductor", "role": role})
    if not authorization.allowed:
        print(json.dumps(authorization.as_dict(), indent=2), file=sys.stderr)
        return 2
    binding = resolve_binding(bundle, role, author_family=task.get("author_family"), required_family="codex")
    if not binding.allowed or binding.details is None:
        print(json.dumps(binding.as_dict(), indent=2), file=sys.stderr)
        return 2
    backend = binding.details
    target_repo = Path(task["target_repo"]).resolve()
    command, working_directory = build_codex_command(backend, host_mode, target_repo)
    if dry_run:
        print(json.dumps({"command": command, "cwd": str(working_directory), "sandbox": "read-only"}, indent=2))
        return 0
    brief = brief_path.read_text(encoding="utf-8")
    prompt = (
        f"Role: {role}\nTask: {task['task_id']}\n"
        "You are a bounded worker, not a conductor. Do not spawn agents or change scope. "
        "The filesystem is read-only; return a structured review, evidence, or applicable patch.\n\n"
        + brief
    )
    completed = subprocess.run(command, cwd=working_directory, input=prompt, text=True, check=False)
    return completed.returncode


def self_test(root: Path) -> Decision:
    """Run dependency-free policy, routing, authorization, and lease checks."""
    bundle = load_policy(root)
    errors, warnings = validate_policy(bundle)
    if errors:
        return Decision(False, "policy validation failed", {"errors": errors, "warnings": warnings})
    critic_for_claude = resolve_binding(bundle, "critic", author_family="claude")
    critic_for_codex = resolve_binding(bundle, "critic", author_family="codex")
    if critic_for_claude.details is None or critic_for_claude.details["family"] != "codex":
        return Decision(False, "critic independence failed for Claude author")
    if critic_for_codex.details is None or critic_for_codex.details["family"] != "claude":
        return Decision(False, "critic independence failed for Codex author")
    task = {
        "schema_version": 1,
        "task_id": "self-test",
        "status": "active",
        "conductor": {"host": "claude-code", "backend": "claude-frontier", "lease_owner": "self-test-owner"},
        "target_repo": str(root),
        "write_scope": ["docs/"],
        "roles_plan": ["implementer", "critic", "verifier"],
        "approvals": {"user": []},
        "dispatch": {"current_role": "critic", "active_workers": 0},
        "author_family": "claude",
        "direct_code_files": 0,
    }
    task_errors, _ = validate_task(bundle, task)
    if task_errors:
        return Decision(False, "self-test task invalid", {"errors": task_errors})
    inside = authorize_action(bundle, task, {"kind": "write", "actor_role": "conductor", "path": str(root / "docs" / "architecture.md")})
    outside = authorize_action(bundle, task, {"kind": "write", "actor_role": "conductor", "path": str(root / "README.md")})
    if not inside.allowed or outside.allowed:
        return Decision(False, "write-scope enforcement failed")
    with tempfile.TemporaryDirectory(prefix="multiagent-self-test-") as directory:
        task_dir = Path(directory) / "task"
        first = acquire_lease(task_dir, "alpha", ttl_seconds=60)
        second = acquire_lease(task_dir, "beta", ttl_seconds=60)
        event = append_event(task_dir, "alpha", {"kind": "self_test"})
        released = release_lease(task_dir, "alpha")
        if not first.allowed or second.allowed or not event.allowed or not released.allowed:
            return Decision(False, "lease enforcement failed")
    return Decision(True, "all self-tests passed", {"warnings": warnings})


def _print_decision(decision: Decision) -> int:
    print(json.dumps(decision.as_dict(), indent=2, ensure_ascii=False))
    return 0 if decision.allowed else 2


def main(argv: list[str] | None = None) -> int:
    """Run the policy-engine command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-policy")
    validate_task_parser = subparsers.add_parser("validate-task")
    validate_task_parser.add_argument("--task", type=Path, required=True)
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--role", required=True)
    resolve_parser.add_argument("--author-family", choices=("claude", "codex"))
    authorize_parser = subparsers.add_parser("authorize")
    authorize_parser.add_argument("--task", type=Path, required=True)
    authorize_parser.add_argument("--action", required=True, help="JSON object")
    for name in ("acquire-lease", "heartbeat-lease", "release-lease"):
        lease_parser = subparsers.add_parser(name)
        lease_parser.add_argument("--task-dir", type=Path, required=True)
        lease_parser.add_argument("--owner", required=True)
        lease_parser.add_argument("--ttl", type=int, default=300)
    event_parser = subparsers.add_parser("append-event")
    event_parser.add_argument("--task-dir", type=Path, required=True)
    event_parser.add_argument("--owner", required=True)
    event_parser.add_argument("--event", required=True, help="JSON object")
    dispatch_parser = subparsers.add_parser("dispatch-codex")
    dispatch_parser.add_argument("--task", type=Path, required=True)
    dispatch_parser.add_argument("--role", required=True)
    dispatch_parser.add_argument("--brief", type=Path, required=True)
    dispatch_parser.add_argument("--host", choices=("native", "wsl"), default="native")
    dispatch_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    bundle = load_policy(args.root)

    if args.command == "validate-policy":
        errors, warnings = validate_policy(bundle)
        return _print_decision(Decision(not errors, "policy valid" if not errors else "policy invalid", {"errors": errors, "warnings": warnings}))
    if args.command == "validate-task":
        errors, warnings = validate_task(bundle, load_document(args.task))
        return _print_decision(Decision(not errors, "task valid" if not errors else "task invalid", {"errors": errors, "warnings": warnings}))
    if args.command == "resolve":
        return _print_decision(resolve_binding(bundle, args.role, args.author_family))
    if args.command == "authorize":
        return _print_decision(authorize_action(bundle, load_document(args.task), json.loads(args.action)))
    if args.command == "acquire-lease":
        return _print_decision(acquire_lease(args.task_dir, args.owner, args.ttl))
    if args.command == "heartbeat-lease":
        return _print_decision(heartbeat_lease(args.task_dir, args.owner, args.ttl))
    if args.command == "release-lease":
        return _print_decision(release_lease(args.task_dir, args.owner))
    if args.command == "append-event":
        return _print_decision(append_event(args.task_dir, args.owner, json.loads(args.event)))
    if args.command == "dispatch-codex":
        return dispatch_codex(bundle, load_document(args.task), args.role, args.brief, args.host, args.dry_run)
    if args.command == "self-test":
        return _print_decision(self_test(args.root))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
