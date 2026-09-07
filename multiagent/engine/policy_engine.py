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
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


POLICY_FILES = ("roles.yaml", "bindings.yaml", "backends.yaml", "routing.yaml", "approvals.yaml")
VALID_EFFORTS = {"low", "medium", "high"}
KNOWN_FAMILIES = {"claude", "codex", "gemini"}
#: A real Gemini model id, e.g. ``gemini-3.6-flash-low``. Anchored so that a
#: vendor name smuggled after the prefix (``gemini-claude-sonnet-4-6``) fails.
GEMINI_MODEL = re.compile(r"gemini-\d[\w.]*(?:-[a-z]+)*")
#: Roles whose dispatch produces the artifact a critic later reviews.
PRODUCING_ROLES = frozenset({"implementer", "bulk_worker"})
#: Sidecar recording which family actually produced the artifact. Both dispatch
#: paths -- this engine and the PreToolUse hook -- write and read the same file,
#: because independence is checked against what was observed producing the work,
#: not against what the contract claims. It sits beside the contract, never beside
#: the lease: those are the same directory by default but need not be.
OBSERVED_AUTHOR_FILE = "observed-author.json"
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

    # A malformed max_active_children either crashes min() at dispatch time or, as 0,
    # silently forbids every dispatch. Reject both here instead.
    for host, adapter in bundle.documents["routing"].get("conductor_adapters", {}).items():
        limit = adapter.get("max_active_children")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            errors.append(
                f"conductor adapter {host} has invalid max_active_children {limit!r}; "
                "expected null or a positive integer"
            )
        # Which backend hosts this adapter can invoke is what decides whether an
        # independent critic exists for it, so it is required, not optional. And
        # it must name hosts the engine can really launch: a declaration the
        # dispatcher cannot honour is how "reachable" quietly stops meaning
        # reachable, which is the exact weakening this check exists to prevent.
        reachable = adapter.get("dispatch_hosts")
        if not isinstance(reachable, list) or not reachable:
            errors.append(f"conductor adapter {host} must declare a non-empty dispatch_hosts list")
        elif not all(isinstance(entry, str) and entry for entry in reachable):
            # Reported, not raised: a malformed entry would otherwise crash the
            # very pass whose job is to describe what is malformed.
            errors.append(f"conductor adapter {host} has non-string dispatch_hosts entries")
        else:
            undeliverable = sorted(set(reachable) - set(WORKER_CLI))
            if undeliverable:
                errors.append(
                    f"conductor adapter {host} declares dispatch_hosts {undeliverable} the engine "
                    f"cannot launch; only {sorted(WORKER_CLI)} have dispatchers"
                )

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
    # The old rule named one conductor host. The rule it was standing in for is
    # this: a host may conduct only if it can actually reach an independent
    # critic and verifier for whichever family authored the artifact.
    for candidate in bundle.bindings.get("conductor", {}).get("candidates", []):
        alias = candidate.get("backend")
        backend = bundle.backends.get(alias)
        if backend is None:
            continue
        host = backend.get("host")
        if host not in bundle.documents["routing"].get("conductor_adapters", {}):
            errors.append(f"conductor candidate {alias} runs on {host}, which has no conductor adapter")
            continue
        for role in ("critic", "verifier"):
            unreachable = [
                family
                for family in sorted(KNOWN_FAMILIES)
                if not resolve_binding(bundle, role, author_family=family, conductor_host=host).allowed
            ]
            if unreachable:
                errors.append(
                    f"conductor candidate {alias} cannot dispatch an independent {role} for "
                    f"artifacts authored by {unreachable}; widen {host}'s dispatch_hosts or "
                    "drop the candidate"
                )
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
    if not {"claude", "codex"} <= bulk_families:
        errors.append("bulk_worker pool must include Claude and Codex families")
    for alias, backend in bundle.backends.items():
        if backend.get("host") == "codex" and not backend.get("writes_mediated"):
            warnings.append(
                f"{alias} writes are gated by the Codex host's own approval prompt, not by this engine"
                if backend.get("write_mode") == "host-approval"
                else f"{alias} is intentionally read-only until mediated writes are validated"
            )
        if backend.get("family") not in KNOWN_FAMILIES:
            errors.append(f"{alias} declares unknown family {backend.get('family')!r}")
        # agy fronts several vendors, so its declared family cannot be inferred from the
        # host. Require both the family and a genuine Gemini model id: a bare "gemini-"
        # prefix check would accept "gemini-claude-sonnet-4-6".
        if backend.get("host") == "agy" and (
            backend.get("family") != "gemini"
            or not GEMINI_MODEL.fullmatch(str(backend.get("model", "")))
        ):
            errors.append(
                f"{alias} must declare family 'gemini' and a gemini-<version> model; "
                "agy also serves other vendors, so a mismatch here silently defeats "
                "different-family independence"
            )
    return errors, warnings


def dispatch_hosts(bundle: PolicyBundle, conductor_host: str) -> set[str]:
    """Return the backend hosts a conductor host's adapter can actually invoke.

    An undeclared adapter, or one without ``dispatch_hosts``, dispatches
    nothing: dispatchability is what makes a critic independent, so an omission
    must deny rather than wave everything through. ``validate_policy`` requires
    the key, so an empty set here means the installation is misconfigured.
    """
    adapter = bundle.documents["routing"].get("conductor_adapters", {}).get(conductor_host, {})
    declared = adapter.get("dispatch_hosts", [])
    if not isinstance(declared, list):
        return set()
    # Drop malformed entries rather than raising: validate_policy reports them,
    # and this function is called from inside that very pass.
    return {entry for entry in declared if isinstance(entry, str) and entry}


def resolve_binding(
    bundle: PolicyBundle,
    role: str,
    author_family: str | None = None,
    required_family: str | None = None,
    conductor_host: str | None = None,
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
    conductor_host:
        Conducting host. When given, candidates its dispatch adapter cannot
        invoke are skipped -- a backend the conductor cannot call is not a
        candidate, however well it matches on family and capability.
    """
    binding = bundle.bindings.get(role)
    if binding is None:
        return Decision(False, f"unknown role: {role}")
    candidates = binding.get("candidates", binding.get("pool", []))
    # Fail closed on independence: a missing or misspelled author_family would
    # otherwise match no candidate's family and silently return the first one,
    # which is how a Codex artifact ends up "independently" reviewed by Codex.
    if binding.get("mode") == "different_family_from_author" and author_family not in KNOWN_FAMILIES:
        return Decision(
            False,
            f"{role} binds different_family_from_author but author_family is "
            f"{author_family!r}; declare one of {sorted(KNOWN_FAMILIES)}",
        )
    reachable = dispatch_hosts(bundle, conductor_host) if conductor_host is not None else None
    for candidate in candidates:
        # A candidate naming an unknown backend is a policy error reported by
        # validate_policy; here it is simply not a candidate. Indexing instead
        # would crash the very validation pass meant to report it.
        backend = bundle.backends.get(candidate.get("backend"))
        if backend is None:
            continue
        family = backend["family"]
        if reachable is not None and backend["host"] not in reachable:
            continue
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
    declared = {
        (bundle.backends[item["backend"]]["host"], item["backend"])
        for item in bundle.bindings.get("conductor", {}).get("candidates", [])
        if item.get("backend") in bundle.backends
    }
    if (conductor.get("host"), conductor.get("backend")) not in declared:
        errors.append(
            f"unsupported conductor {conductor.get('host')!r}/{conductor.get('backend')!r}: "
            f"declare one of {sorted(declared)} in bindings.yaml. A host may conduct only with "
            "a conductor adapter that reaches an independent critic and verifier."
        )
    elif not dispatch_hosts(bundle, conductor["host"]):
        # Unconditional: this is the admission gate the conductor is told to run
        # before taking a lease, so it cannot depend on which roles happen to be
        # planned. A host that can dispatch nothing may not conduct anything.
        errors.append(
            f"conductor host {conductor['host']} declares no usable dispatch_hosts, "
            "so it can dispatch no worker at all"
        )
    else:
        # Catch an unfulfillable plan now rather than at the dispatch that needs it.
        for role in sorted(set(task.get("roles_plan", []) or []) & set(bundle.bindings)):
            reachable = resolve_binding(
                bundle,
                role,
                author_family=task.get("author_family"),
                conductor_host=conductor["host"],
            )
            if not reachable.allowed:
                errors.append(f"conductor host {conductor['host']} cannot dispatch {role}: {reachable.reason}")
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
    planned = task.get("roles_plan", [])
    if isinstance(planned, list) and ({"critic", "verifier"} & set(planned)):
        if task.get("author_family") not in KNOWN_FAMILIES:
            errors.append(
                "tasks planning critic or verifier must declare author_family as one of "
                f"{sorted(KNOWN_FAMILIES)}; got {task.get('author_family')!r}"
            )
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


def held_worker_slots(task_dir: Path | None) -> int:
    """Return the worker slots currently recorded on a task's lease."""
    if task_dir is None:
        return 0
    lease_path = task_dir / "lease.json"
    if not lease_path.exists():
        return 0
    try:
        return int(load_document(lease_path).get("active_workers", 0))
    except (OSError, ValueError):
        return 0


def authorize_action(
    bundle: PolicyBundle,
    task: dict[str, Any],
    action: dict[str, Any],
    task_dir: Path | None = None,
) -> Decision:
    """Authorize a normalized orchestration action against a task contract.

    ``task_dir`` lets the native-spawn path see the slots CLI dispatches hold. Without
    it the two paths check the same ceiling against different numbers, and each can
    fill it independently.
    """
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
        host = task["conductor"]["host"]
        limit = worker_limit(bundle, host)
        if limit is None:
            return Decision(False, f"no conductor adapter configured for host: {host}")
        # Both kinds of worker charge one ceiling, from whichever side asks: the
        # contract's count of natively-spawned workers plus the slots CLI
        # dispatches hold on the lease.
        in_flight = task["dispatch"]["active_workers"] + held_worker_slots(task_dir)
        if in_flight >= limit:
            return Decision(False, f"active-worker limit reached for {host} ({in_flight}/{limit})")
        author_family = task.get("author_family") if role in {"critic", "verifier"} else None
        return resolve_binding(bundle, role, author_family=author_family, conductor_host=host)

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


def worker_limit(bundle: PolicyBundle, host: str) -> int | None:
    """Return the simultaneous-worker ceiling for a conducting host.

    ``None`` means the host has no conductor adapter and so may not dispatch at
    all -- absent, not unlimited.
    """
    routing = bundle.documents["routing"]
    adapter = routing.get("conductor_adapters", {}).get(host)
    if adapter is None:
        return None
    limit = routing["defaults"]["max_fanout"]
    cap = adapter.get("max_active_children")
    return min(limit, cap) if cap is not None else limit


# What the worker count is for, since the machinery below only makes sense against
# a stated threat model. It bounds ACCIDENTAL fan-out -- a conductor spawning far
# more workers than intended, burning quota and swamping the host -- for one
# cooperating conductor holding one lease on one machine. It is NOT a security
# boundary and NOT a hard guarantee: a SIGKILLed dispatcher leaves its child
# running and its slot held, and a natively-spawned worker is only counted if the
# conductor says so. Anything that would need to survive a hostile or crashed
# participant needs a supervisor, not a JSON counter. Do not add machinery here
# that implies a stronger promise than this paragraph makes.


@contextmanager
def _lease_lock(task_dir: Path, timeout: float = 10.0) -> Any:
    """Hold an exclusive lock over one task's lease file.

    Fan-out means several ``dispatch-worker`` processes mutate one lease at once,
    which is exactly when an unlocked read-modify-write loses an increment.
    ``O_EXCL`` is the portable primitive here; ``fcntl`` would exclude the
    native-Windows host this engine still supports.

    A stuck lock is **not** stolen on a timer. Time-based stealing races the
    holder it assumes is dead -- a suspended process resumes mid-mutation, and
    its own release then unlinks a successor's lock. The lock is held only for a
    small read-modify-write, so a timeout means a crash, and saying so is more
    useful than silently continuing without exclusion.
    """
    lock_path = task_dir / "lease.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{lock_path} is still held after {timeout}s. The lock is taken only for "
                    "a brief update, so it is most likely stale -- though a live holder may "
                    "simply be stalled or suspended. Stop or verify every process for this "
                    "task, then remove the file by hand. Lease expiry alone will not clear it."
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _write_lease(lease_path: Path, payload: dict[str, Any]) -> None:
    """Replace a lease file atomically.

    A partial ``write_text`` leaves JSON that no later read can parse, which
    strands the task; ``os.replace`` makes the update all-or-nothing so an
    unlocked reader sees either the old payload or the new one.
    """
    temporary = lease_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, lease_path)


def _mutate_lease(task_dir: Path, mutate: Any) -> Decision:
    """Apply ``mutate`` to the lease payload under an exclusive lock."""
    lease_path = task_dir / "lease.json"
    try:
        with _lease_lock(task_dir):
            if not lease_path.exists():
                return Decision(False, f"no task lease in {task_dir}; acquire one before dispatching")
            payload = load_document(lease_path)
            decision = mutate(payload)
            if decision.allowed:
                _write_lease(lease_path, payload)
            return decision
    except TimeoutError as error:
        return Decision(False, str(error))


def claim_worker_slot(task_dir: Path, owner: str, limit: int, reserved: int = 0) -> Decision:
    """Take one simultaneous-worker slot, recorded on the lease.

    The count lives on the lease rather than in the task contract because the
    contract is written by the conductor being limited. A self-reported count
    left at zero permits unlimited workers; a count the engine increments does
    not. The lease is already the one file with a single owner, so it is where a
    number that must not be forged belongs.

    ``reserved`` is the contract's own count of natively-spawned workers, which
    never reach this engine. Both are charged against **one** ceiling: counting
    them separately would let a host run ``limit`` workers of each kind.

    The returned ``generation`` identifies the lease this claim belongs to, so a
    release cannot decrement a counter that a later acquisition started.
    """

    def mutate(payload: dict[str, Any]) -> Decision:
        if payload.get("owner") != owner or float(payload.get("expires_epoch", 0)) <= time.time():
            return Decision(False, "a live matching lease is required")
        active = int(payload.get("active_workers", 0))
        if reserved + active >= limit:
            return Decision(False, f"active-worker limit reached ({reserved + active}/{limit})")
        payload["active_workers"] = active + 1
        return Decision(
            True,
            "worker slot claimed",
            {
                "active_workers": active + 1,
                "reserved": reserved,
                "limit": limit,
                "generation": payload.get("acquired_at"),
            },
        )

    return _mutate_lease(task_dir, mutate)


def release_worker_slot(task_dir: Path, owner: str, generation: str) -> Decision:
    """Give back one simultaneous-worker slot.

    Deliberately accepts an **expired** lease: a worker that outlived its lease
    still stopped occupying a slot, and refusing here would leak capacity for the
    rest of the task. What it will not do is decrement across a reacquisition --
    a mismatched ``generation`` means this slot belongs to a lease that no longer
    exists, and the current one never counted it. ``generation`` is required, not
    optional: an omitted one silently restores the cross-lease decrement it exists
    to prevent.
    """

    def mutate(payload: dict[str, Any]) -> Decision:
        if payload.get("owner") != owner:
            return Decision(False, "lease owner mismatch")
        if payload.get("acquired_at") != generation:
            return Decision(False, "lease was reacquired since this slot was claimed")
        # Floor at zero: a release without a matching claim is a bug, but a
        # negative count would silently raise the ceiling for every later claim.
        payload["active_workers"] = max(0, int(payload.get("active_workers", 0)) - 1)
        return Decision(True, "worker slot released", {"active_workers": payload["active_workers"]})

    return _mutate_lease(task_dir, mutate)


def acquire_lease(task_dir: Path, owner: str, ttl_seconds: int = 300) -> Decision:
    """Acquire an exclusive task lease, replacing only an expired lease.

    Takes the same lock as every other lease mutation. Acquiring outside it could
    replace a lease that a concurrent heartbeat or slot claim had already read,
    which would then write its stale payload back over the new one.
    """
    task_dir.mkdir(parents=True, exist_ok=True)
    lease_path = task_dir / "lease.json"
    try:
        with _lease_lock(task_dir):
            now = time.time()
            if lease_path.exists():
                existing = load_document(lease_path)
                if float(existing.get("expires_epoch", 0)) > now:
                    return Decision(False, f"task is leased by {existing.get('owner', 'unknown')}")
                lease_path.replace(task_dir / f"lease.stale.{int(now)}.json")
            payload = {
                "owner": owner,
                "acquired_at": utc_now(),
                "heartbeat_at": utc_now(),
                "expires_epoch": now + ttl_seconds,
                "active_workers": 0,
            }
            _write_lease(lease_path, payload)
            return Decision(True, "lease acquired", payload)
    except TimeoutError as error:
        return Decision(False, str(error))


def heartbeat_lease(
    task_dir: Path, owner: str, ttl_seconds: int = 300, generation: str | None = None
) -> Decision:
    """Extend a lease owned by ``owner``.

    Locked like the slot counters: an unlocked rewrite here would restore a whole
    payload read before a concurrent claim, erasing that claim's increment.

    Pass ``generation`` from any long-lived renewer. Without it, a heartbeat left
    over from a previous lease keeps renewing whatever lease exists now, which is
    how an abandoned dispatcher props up a lease it no longer has any part in.
    """

    def mutate(payload: dict[str, Any]) -> Decision:
        if payload.get("owner") != owner:
            return Decision(False, "lease owner mismatch")
        if generation is not None and payload.get("acquired_at") != generation:
            return Decision(False, "lease was reacquired; this renewer no longer owns it")
        payload["heartbeat_at"] = utc_now()
        payload["expires_epoch"] = time.time() + ttl_seconds
        return Decision(True, "lease heartbeat recorded", dict(payload))

    return _mutate_lease(task_dir, mutate)


def release_lease(task_dir: Path, owner: str) -> Decision:
    """Release a lease owned by ``owner``.

    Locked like the rest of the lifecycle, and refuses while workers are still
    counted: deleting the lease under a running dispatch would destroy the record
    of the slot it holds.
    """
    lease_path = task_dir / "lease.json"
    try:
        with _lease_lock(task_dir):
            if not lease_path.exists():
                return Decision(False, "task has no lease")
            payload = load_document(lease_path)
            if payload.get("owner") != owner:
                return Decision(False, "lease owner mismatch")
            active = int(payload.get("active_workers", 0))
            if active:
                return Decision(False, f"{active} worker slot(s) still held; release them first")
            lease_path.unlink()
            return Decision(True, "lease released")
    except TimeoutError as error:
        return Decision(False, str(error))


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


@dataclass(frozen=True, slots=True)
class WorkerCommand:
    """How to launch one worker, and how strongly it is contained.

    Attributes
    ----------
    program:
        Executable name, resolved against PATH later.
    args:
        Arguments, excluding the prompt.
    enforcement:
        What actually prevents this worker from writing. Reported verbatim, so
        it must never claim more than the flags deliver.
    prompt_via:
        ``stdin`` or ``argv``; the prompt is appended for ``argv``.
    isolated_cwd:
        Run in a throwaway directory instead of the target repository.
    """

    program: str
    args: list[str]
    enforcement: str
    prompt_via: str = "stdin"
    isolated_cwd: bool = False


def _codex_cli(backend: dict[str, Any], role: str | None = None) -> WorkerCommand:
    """Build the Codex worker command, made read-only by its own OS sandbox."""
    return WorkerCommand(
        "codex",
        [
            "exec", "--sandbox", "read-only", "--model", str(backend["model"]),
            "-c", f'model_reasoning_effort="{backend["effort"]}"',
            "--skip-git-repo-check", "--ephemeral", "--color", "never", "-",
        ],
        "os-sandbox-read-only",
    )


def _claude_cli(backend: dict[str, Any], role: str | None = None) -> WorkerCommand:
    """Build the Claude worker command.

    ``--tools`` restricts the tool surface itself; ``--allowedTools`` would only
    grant permissions while leaving Bash and every inherited MCP tool present,
    which is not containment. ``--strict-mcp-config`` with no ``--mcp-config``
    drops the inherited MCP servers too. This still binds the agent rather than
    the process -- a settings-level hook could act outside it -- so it is not
    labelled as a sandbox.

    No role gets ``Bash`` here, including ``verifier``. Granting it would buy test
    execution at the price of an uncontained write path in the target repository,
    which is the containment this command exists to provide. The consequence is
    real and must not be papered over: a CLI-dispatched Claude verifier can read
    and reason about tests but cannot run them. Route executable verification to
    a Codex verifier, whose read-only sandbox blocks writes rather than commands,
    or to a natively-spawned Claude subagent under the PreToolUse hook.
    """
    return WorkerCommand(
        "claude",
        [
            "--tools", "Read,Grep,Glob", "--strict-mcp-config",
            "--effort", str(backend["effort"]), "--model", str(backend["model"]), "-p",
        ],
        "restricted-tool-surface",
    )


def _agy_cli(backend: dict[str, Any], role: str | None = None) -> WorkerCommand:
    """Build the Gemini worker command.

    Headless ``agy`` takes its prompt as one argument, not on stdin, and times
    out when told to walk a directory -- so it runs in a throwaway directory
    with no ``--add-dir``, and the brief must inline whatever it needs to read.

    **This builder is deliberately unreachable**: ``agy`` is absent from every
    adapter's ``dispatch_hosts`` until two things are established, neither of
    which a throwaway cwd provides on its own. First, containment: ``--sandbox``
    restricts the terminal but is not a filesystem boundary, so an absolute path
    still escapes the temporary directory -- which is why
    ``--dangerously-skip-permissions`` is *not* passed here, even though System A
    passes it. Second, a successful completion has never been observed through
    this path. Re-add ``agy`` to ``dispatch_hosts`` only after both hold, and note
    the argv limit before doing so: Windows caps a command line at 32,767
    characters, so an inlined brief must move to stdin or a file in the cwd.
    """
    return WorkerCommand(
        "agy",
        [
            "--model", str(backend["model"]), "--effort", str(backend["effort"]),
            "--sandbox", "--prompt",
        ],
        "isolated-cwd-containment-unverified",
        prompt_via="argv",
        isolated_cwd=True,
    )


#: Hosts the engine can actually launch, keyed by backend ``host``. Both
#: `validate_policy` and `resolve_binding`'s dispatchability filter read this, so
#: a `dispatch_hosts` entry with no builder here is a policy error caught at
#: validation rather than a ValueError raised mid-dispatch.
WORKER_CLI = {"codex": _codex_cli, "claude-code": _claude_cli, "agy": _agy_cli}


def worker_cli_args(backend: dict[str, Any], role: str | None = None) -> WorkerCommand:
    """Return the launch specification for a resolved backend and role."""
    builder = WORKER_CLI.get(backend["host"])
    if builder is None:
        raise ValueError(f"host {backend['host']!r} has no engine dispatcher")
    return builder(backend, role)


def build_worker_command(
    backend: dict[str, Any], host_mode: str, target_repo: Path, role: str | None = None
) -> tuple[list[str], WorkerCommand]:
    """Resolve a worker launch specification into a native or WSL argv."""
    spec = worker_cli_args(backend, role)
    if host_mode == "native":
        executable = (
            shutil.which(f"{spec.program}.cmd") or shutil.which(spec.program)
            if os.name == "nt"
            else shutil.which(spec.program)
        )
        if executable is None:
            raise FileNotFoundError(f"{spec.program} executable is not on PATH")
        return [executable, *spec.args], spec
    if host_mode == "wsl":
        if spec.isolated_cwd:
            # `wsl --cd` fixes the working directory here, before the caller's
            # temporary-directory branch can apply. Silently ignoring that would
            # start an "isolated" worker inside the target repository while the
            # dry run still claimed isolation, so refuse instead.
            raise NotImplementedError(
                f"{spec.program} requires an isolated working directory, which the WSL "
                "launcher cannot provide; dispatch it natively"
            )
        wsl = shutil.which("wsl.exe")
        if wsl is None:
            raise FileNotFoundError("wsl.exe is not on PATH")
        converted = subprocess.run(
            [wsl, "wslpath", "-a", str(target_repo)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return [wsl, "--cd", converted, "--exec", spec.program, *spec.args], spec
    raise ValueError(f"unsupported worker host mode: {host_mode}")


class UnreadableAuthorRecord(Exception):
    """The authorship sidecar exists but cannot be trusted."""


def observed_author_family(contract_dir: Path) -> str | None:
    """Return the family recorded as producing this task's artifact.

    ``None`` means genuinely no record -- no producing worker ran. A record that
    exists but is corrupt, unreadable, or names an unknown family raises instead:
    collapsing that into ``None`` would make damaged evidence indistinguishable
    from honest absence, and the caller would wave the review through.
    """
    sidecar = contract_dir / OBSERVED_AUTHOR_FILE
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UnreadableAuthorRecord(f"{sidecar} cannot be read: {error}") from error
    family = payload.get("family") if isinstance(payload, dict) else None
    # isinstance first: an unhashable value (a list, a dict) would raise TypeError
    # on the set lookup instead of reaching the structured denial.
    if not isinstance(family, str) or family not in KNOWN_FAMILIES:
        raise UnreadableAuthorRecord(f"{sidecar} records an unknown family: {family!r}")
    return family


def dispatch_worker(
    bundle: PolicyBundle,
    task: dict[str, Any],
    role: str,
    brief_path: Path,
    host_mode: str,
    dry_run: bool,
    required_family: str | None = None,
    task_dir: Path | None = None,
    contract_dir: Path | None = None,
) -> int:
    """Dispatch one bounded worker as a subprocess.

    The backend is whichever the binding resolves under the conducting host, so
    a Codex conductor reaches a Claude critic through the ``claude`` CLI exactly
    as a Claude conductor reaches a Codex one -- neither host's native child API
    is involved.

    A real dispatch holds a worker slot on the task lease for its duration, so
    ``task_dir`` must contain a live lease owned by ``conductor.lease_owner``. A
    dry run resolves and prints without claiming anything.
    """
    # The contract's declared role and the dispatched role must agree. The hook
    # reads `current_role` while this command reads `--role`, so a mismatch means
    # the two enforcement paths disagree about what is running -- and the one
    # that checks independence is the one being told a different story.
    declared_role = task.get("dispatch", {}).get("current_role")
    if declared_role != role:
        print(json.dumps(Decision(
            False,
            f"dispatch.current_role is {declared_role!r} but --role is {role!r}; "
            "set the contract's current_role before dispatching",
        ).as_dict(), indent=2), file=sys.stderr)
        return 2
    authorization = authorize_action(
        bundle, task, {"kind": "spawn_worker", "actor_role": "conductor", "role": role}, task_dir=task_dir
    )
    if not authorization.allowed:
        print(json.dumps(authorization.as_dict(), indent=2), file=sys.stderr)
        return 2
    # Independence is checked against what was observed producing the artifact,
    # not against what the contract asserts. The hook already does this for
    # natively-spawned reviewers; without it here, a stale author_family picks a
    # reviewer of the same family that actually wrote the code.
    if role in {"critic", "verifier"} and contract_dir is not None:
        try:
            seen = observed_author_family(contract_dir)
        except UnreadableAuthorRecord as error:
            print(json.dumps(Decision(
                False, f"{role} blocked: {error}"
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        if seen is None:
            # No producing worker ran, so the conductor authored the artifact and
            # its family is the honest answer -- the same fallback the hook uses.
            # Trusting the declaration here instead would let a conductor review
            # its own work simply by declaring another family.
            conductor_backend = bundle.backends.get(task["conductor"]["backend"])
            seen = conductor_backend["family"] if conductor_backend else None
        if seen is not None and seen != task.get("author_family"):
            print(json.dumps(Decision(
                False,
                f"{role} blocked: contract declares author_family="
                f"{task.get('author_family')!r} but {seen!r} was observed producing this "
                "task's artifact; correct the contract before dispatching a reviewer",
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
    binding = resolve_binding(
        bundle,
        role,
        author_family=task.get("author_family"),
        required_family=required_family,
        conductor_host=task["conductor"]["host"],
    )
    if not binding.allowed or binding.details is None:
        print(json.dumps(binding.as_dict(), indent=2), file=sys.stderr)
        return 2
    backend = binding.details
    target_repo = Path(task["target_repo"]).resolve()
    command, spec = build_worker_command(backend, host_mode, target_repo, role)
    if dry_run:
        print(json.dumps(
            {
                "command": command,
                "cwd": "<isolated temporary directory>" if spec.isolated_cwd else str(target_repo),
                "backend": backend["host"],
                "enforcement": spec.enforcement,
                "prompt_via": spec.prompt_via,
            },
            indent=2,
        ))
        return 0
    limit = worker_limit(bundle, task["conductor"]["host"])
    owner = task["conductor"]["lease_owner"]
    slots = task_dir if task_dir is not None else Path(".")
    # Natively-spawned workers never reach this engine, so the contract's count
    # of them is charged against the same ceiling as this one.
    reserved = int(task.get("dispatch", {}).get("active_workers", 0))
    claimed = claim_worker_slot(slots, owner, limit, reserved)
    if not claimed.allowed:
        print(json.dumps(claimed.as_dict(), indent=2), file=sys.stderr)
        return 2
    generation = (claimed.details or {}).get("generation")
    # A worker easily outlives the default lease TTL -- this review did. Without
    # a heartbeat the lease expires mid-run, another conductor may take the task
    # while the worker is still going, and the release lands on a lease that
    # never counted it.
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_until, args=(slots, owner, generation, stop_heartbeat), daemon=True
    )
    heartbeat.start()
    try:
        status = _run_worker(command, spec, task, role, brief_path, target_repo)
        if status == 0 and role in PRODUCING_ROLES and contract_dir is not None:
            # The hook records authorship only for natively-spawned workers.
            # Without this, an artifact produced here is later attributed to
            # whichever family the conductor happens to be, and the independence
            # check reviews the wrong author. Same filename and shape the hook
            # reads, beside the contract the hook reads it from.
            #
            # Recorded only AFTER a successful run. Recording intent up front
            # meant a worker that exited non-zero without producing anything
            # still overwrote the real author's record -- and the denial that
            # followed told the conductor to "correct" the declaration to match,
            # handing the untouched artifact to a same-family reviewer.
            #
            # ponytail: last successful producer wins. Mixed-family artifacts
            # collapse to one family; record a list if that ever matters.
            (contract_dir / OBSERVED_AUTHOR_FILE).write_text(
                json.dumps(
                    {"family": backend["family"], "source": f"{role} via dispatch-worker"}, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
        return status
    finally:
        # A worker that crashed still freed its slot, and leaking one would
        # shrink the ceiling for the rest of the task. The release can still be
        # refused across a reacquired lease -- reported below, never silent.
        stop_heartbeat.set()
        heartbeat.join(timeout=5)
        released = release_worker_slot(slots, owner, generation)
        if not released.allowed:
            # Never silent: a slot that failed to come back shrinks the ceiling
            # for every later dispatch, and the cause is not visible elsewhere.
            print(f"warning: worker slot not released: {released.reason}", file=sys.stderr)


#: Lease TTL used while a dispatched worker runs, and the interval at which it is
#: renewed. The renewal must comfortably beat the expiry it is refreshing.
WORKER_LEASE_TTL = 300
HEARTBEAT_INTERVAL = 60.0


def _heartbeat_until(task_dir: Path, owner: str, generation: str, stop: threading.Event) -> None:
    """Renew the task lease until ``stop`` is set, for one lease generation only."""
    while not stop.wait(HEARTBEAT_INTERVAL):
        try:
            if not heartbeat_lease(task_dir, owner, WORKER_LEASE_TTL, generation).allowed:
                return  # The lease moved on; renewing it is no longer our business.
        except (OSError, TimeoutError, ValueError):
            # A heartbeat that cannot be written must not kill the worker it is
            # protecting; the next tick retries.
            continue


def _run_worker(
    command: list[str],
    spec: WorkerCommand,
    task: dict[str, Any],
    role: str,
    brief_path: Path,
    target_repo: Path,
) -> int:
    """Launch a resolved worker command and return its exit status."""
    brief = brief_path.read_text(encoding="utf-8")
    prompt = (
        f"Role: {role}\nTask: {task['task_id']}\n"
        "You are a bounded worker, not a conductor. Do not spawn agents or change scope. "
        "Do not write to the filesystem; return a structured review, evidence, or an "
        f"applicable patch instead. (Enforcement here: {spec.enforcement}.)\n\n"
        + brief
    )
    if spec.prompt_via == "argv":
        command = [*command, prompt]

    def run(working_directory: str | Path) -> int:
        """Launch the worker, feeding the prompt the way its CLI expects."""
        if spec.prompt_via == "argv":
            return subprocess.run(
                command, cwd=working_directory, stdin=subprocess.DEVNULL, text=True, check=False
            ).returncode
        return subprocess.run(
            command, cwd=working_directory, input=prompt, text=True, check=False
        ).returncode

    if not spec.isolated_cwd:
        return run(target_repo)
    with tempfile.TemporaryDirectory(prefix="multiagent-worker-") as isolated:
        return run(isolated)


def self_test(root: Path) -> Decision:
    """Run dependency-free policy, routing, authorization, and lease checks."""
    bundle = load_policy(root)
    errors, warnings = validate_policy(bundle)
    if errors:
        return Decision(False, "policy validation failed", {"errors": errors, "warnings": warnings})
    critic_for_claude = resolve_binding(bundle, "critic", author_family="claude")
    critic_for_codex = resolve_binding(bundle, "critic", author_family="codex")
    # An undeclared or misspelled author must not resolve at all. Without this,
    # the family filter matches nothing and the first candidate is returned --
    # which silently lets a family review its own artifact.
    for role in ("critic", "verifier"):
        for bad in (None, "", "cluade", "gemini-flash"):
            if resolve_binding(bundle, role, author_family=bad).allowed:
                return Decision(False, f"{role} resolved with undeclared author_family {bad!r}")
    if critic_for_claude.details is None or critic_for_claude.details["family"] != "codex":
        return Decision(False, "critic independence failed for Claude author")
    if critic_for_codex.details is None or critic_for_codex.details["family"] != "claude":
        return Decision(False, "critic independence failed for Codex author")
    # A conductor is only as independent as its dispatch reach. Confirm both that
    # a Codex conductor really can reach a Claude critic, and that it would be
    # refused the moment its adapter could only reach Codex.
    codex_critic = resolve_binding(bundle, "critic", author_family="codex", conductor_host="codex")
    if codex_critic.details is None or codex_critic.details["family"] == "codex":
        return Decision(False, "a Codex conductor did not resolve a non-Codex critic")
    adapters_under_test = bundle.documents["routing"]["conductor_adapters"]
    saved_reach = list(adapters_under_test["codex"]["dispatch_hosts"])
    try:
        adapters_under_test["codex"]["dispatch_hosts"] = ["codex"]
        if resolve_binding(bundle, "critic", author_family="codex", conductor_host="codex").allowed:
            return Decision(False, "a Codex-only adapter still resolved a critic for a Codex artifact")
        if not any("independent critic" in message for message in validate_policy(bundle)[0]):
            return Decision(False, "validate_policy accepted a conductor that cannot reach a critic")
        # A host may declare only what the engine can really launch, or
        # "reachable" stops meaning reachable and dispatch raises instead.
        adapters_under_test["codex"]["dispatch_hosts"] = ["codex", "no-such-host"]
        if not any("cannot launch" in message for message in validate_policy(bundle)[0]):
            return Decision(False, "validate_policy accepted a dispatch host with no dispatcher")
        adapters_under_test["codex"].pop("dispatch_hosts")
        if resolve_binding(bundle, "critic", author_family="codex", conductor_host="codex").allowed:
            return Decision(False, "a missing dispatch_hosts failed open instead of denying")
        # The admission gate cannot depend on which roles were planned.
        unreachable_task = {
            "schema_version": 1, "task_id": "self-test-reach", "status": "active",
            "conductor": {"host": "codex", "backend": "codex-frontier", "lease_owner": "o"},
            "target_repo": str(root), "write_scope": [], "roles_plan": ["runner"],
            "approvals": {"user": []}, "dispatch": {"current_role": None, "active_workers": 0},
        }
        if not validate_task(bundle, unreachable_task)[0]:
            return Decision(False, "validate_task admitted a conductor that can dispatch nothing")
    finally:
        adapters_under_test["codex"]["dispatch_hosts"] = saved_reach

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
    routing = bundle.documents["routing"]
    adapters = routing.get("conductor_adapters", {})
    saved_fanout = routing["defaults"]["max_fanout"]
    saved_adapters = dict(adapters)
    saved_children = adapters.get("claude-code", {}).get("max_active_children")
    spawn = {"kind": "spawn_worker", "actor_role": "conductor", "role": "critic"}
    try:
        routing["defaults"]["max_fanout"] = 2
        adapters["claude-code"]["max_active_children"] = 1
        task["dispatch"]["active_workers"] = 0
        if not authorize_action(bundle, task, spawn).allowed:
            return Decision(False, "adapter limit denied a dispatch below capacity")
        task["dispatch"]["active_workers"] = 1
        if authorize_action(bundle, task, spawn).allowed:
            return Decision(False, "adapter limit did not cap max_fanout")
        # A declared adapter with an unmeasured capacity falls back to max_fanout.
        adapters["claude-code"]["max_active_children"] = None
        if not authorize_action(bundle, task, spawn).allowed:
            return Decision(False, "null max_active_children did not fall back to max_fanout")
        # An undeclared host has no enforceable dispatch contract: deny, never fall back.
        adapters.pop("claude-code")
        if authorize_action(bundle, task, spawn).allowed:
            return Decision(False, "a host without a conductor adapter was allowed to dispatch")
        # A malformed limit must be a policy error, not a min() crash or a silent zero.
        adapters["claude-code"] = {"max_active_children": 0}
        if not any("max_active_children" in message for message in validate_policy(bundle)[0]):
            return Decision(False, "validate_policy accepted max_active_children of 0")
        adapters["claude-code"] = {"max_active_children": "3"}
        if not any("max_active_children" in message for message in validate_policy(bundle)[0]):
            return Decision(False, "validate_policy accepted a non-integer max_active_children")
    finally:
        routing["defaults"]["max_fanout"] = saved_fanout
        adapters.clear()
        adapters.update(saved_adapters)
        if "claude-code" in adapters:
            adapters["claude-code"]["max_active_children"] = saved_children
        task["dispatch"]["active_workers"] = 0
    with tempfile.TemporaryDirectory(prefix="multiagent-self-test-") as directory:
        task_dir = Path(directory) / "task"
        first = acquire_lease(task_dir, "alpha", ttl_seconds=60)
        second = acquire_lease(task_dir, "beta", ttl_seconds=60)
        event = append_event(task_dir, "alpha", {"kind": "self_test"})
        # The worker count is the engine's, not the conductor's: it must rise on
        # claim, refuse past the ceiling, fall on release, and reject a claim
        # from anyone but the lease owner.
        era = load_document(task_dir / "lease.json")["acquired_at"]
        if not claim_worker_slot(task_dir, "alpha", 2).allowed:
            return Decision(False, "first worker slot was refused below the limit")
        if not claim_worker_slot(task_dir, "alpha", 2).allowed:
            return Decision(False, "second worker slot was refused below the limit")
        if claim_worker_slot(task_dir, "alpha", 2).allowed:
            return Decision(False, "worker slots exceeded the limit")
        if claim_worker_slot(task_dir, "beta", 2).allowed:
            return Decision(False, "a non-owner claimed a worker slot")
        release_worker_slot(task_dir, "alpha", era)
        if not claim_worker_slot(task_dir, "alpha", 2).allowed:
            return Decision(False, "releasing a slot did not free capacity")
        for _ in range(5):
            release_worker_slot(task_dir, "alpha", era)
        if load_document(task_dir / "lease.json")["active_workers"] != 0:
            return Decision(False, "excess releases drove the worker count below zero")
        # Native and CLI workers share one ceiling, not one each.
        if claim_worker_slot(task_dir, "alpha", 2, reserved=2).allowed:
            return Decision(False, "contract-declared workers were not charged against the ceiling")
        # Concurrent claims must not lose an increment; without the lock both of
        # these read the same count and write the same value.
        generation = load_document(task_dir / "lease.json").get("acquired_at")
        outcomes: list[bool] = []
        threads = [
            threading.Thread(target=lambda: outcomes.append(claim_worker_slot(task_dir, "alpha", 4).allowed))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if load_document(task_dir / "lease.json")["active_workers"] != sum(outcomes):
            return Decision(False, "concurrent claims lost an increment")
        # A release must not decrement a counter a later acquisition started.
        if release_worker_slot(task_dir, "alpha", generation="1999-01-01T00:00:00Z").allowed:
            return Decision(False, "a stale-generation release decremented the live lease")
        for _ in range(sum(outcomes)):
            release_worker_slot(task_dir, "alpha", generation=generation)
        if load_document(task_dir / "lease.json")["active_workers"] != 0:
            return Decision(False, "matching-generation releases did not drain the count")
        # The native path must see slots the CLI path holds, or each fills the
        # ceiling on its own and the total is double.
        claim_worker_slot(task_dir, "alpha", 2)
        native = {**task, "dispatch": {"current_role": "critic", "active_workers": 1}}
        routing["defaults"]["max_fanout"] = 2
        try:
            # One native worker declared plus one slot held on the lease fills a
            # ceiling of two; counting either alone would let it through.
            if authorize_action(bundle, native, spawn, task_dir=task_dir).allowed:
                return Decision(False, "a native spawn ignored the slots held on the lease")
            if not authorize_action(bundle, native, spawn).allowed:
                return Decision(False, "the contract-only count changed meaning without a task_dir")
        finally:
            routing["defaults"]["max_fanout"] = saved_fanout
        # A lease may not be dropped while it still accounts for running workers.
        if release_lease(task_dir, "alpha").allowed:
            return Decision(False, "the lease was released while a worker slot was held")
        release_worker_slot(task_dir, "alpha", era)
        # A renewer from a previous lease must not prop up its successor.
        if heartbeat_lease(task_dir, "alpha", 60, generation="1999-01-01T00:00:00Z").allowed:
            return Decision(False, "a stale-generation heartbeat renewed the live lease")
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
    resolve_parser.add_argument("--author-family", choices=("claude", "codex", "gemini"))
    # Required: an unfiltered resolution answers a question nobody can act on,
    # and reads as an endorsement of a backend the caller may not be able to reach.
    resolve_parser.add_argument("--conductor-host", required=True)
    authorize_parser = subparsers.add_parser("authorize")
    authorize_parser.add_argument("--task", type=Path, required=True)
    authorize_parser.add_argument("--action", required=True, help="JSON object")
    # This is the Codex conductor's only pre-flight check, so it must see the same
    # numbers the hook does; without the lease it counts natively-spawned workers
    # alone and clears a spawn that CLI slots have already filled the ceiling for.
    authorize_parser.add_argument(
        "--task-dir", type=Path, help="lease location; defaults to the task file's directory"
    )
    for name in ("acquire-lease", "heartbeat-lease", "release-lease"):
        lease_parser = subparsers.add_parser(name)
        lease_parser.add_argument("--task-dir", type=Path, required=True)
        lease_parser.add_argument("--owner", required=True)
        lease_parser.add_argument("--ttl", type=int, default=300)
    event_parser = subparsers.add_parser("append-event")
    event_parser.add_argument("--task-dir", type=Path, required=True)
    event_parser.add_argument("--owner", required=True)
    event_parser.add_argument("--event", required=True, help="JSON object")
    dispatch_parser = subparsers.add_parser("dispatch-worker")
    dispatch_parser.add_argument("--task", type=Path, required=True)
    dispatch_parser.add_argument("--role", required=True)
    dispatch_parser.add_argument("--brief", type=Path, required=True)
    dispatch_parser.add_argument("--host", choices=("native", "wsl"), default="native")
    dispatch_parser.add_argument("--required-family", choices=sorted(KNOWN_FAMILIES))
    dispatch_parser.add_argument(
        "--task-dir", type=Path, help="lease location; defaults to the task file's directory"
    )
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
        return _print_decision(
            resolve_binding(bundle, args.role, args.author_family, conductor_host=args.conductor_host)
        )
    if args.command == "authorize":
        return _print_decision(authorize_action(
            bundle, load_document(args.task), json.loads(args.action),
            task_dir=args.task_dir or args.task.parent,
        ))
    if args.command == "acquire-lease":
        return _print_decision(acquire_lease(args.task_dir, args.owner, args.ttl))
    if args.command == "heartbeat-lease":
        return _print_decision(heartbeat_lease(args.task_dir, args.owner, args.ttl))
    if args.command == "release-lease":
        return _print_decision(release_lease(args.task_dir, args.owner))
    if args.command == "append-event":
        return _print_decision(append_event(args.task_dir, args.owner, json.loads(args.event)))
    if args.command == "dispatch-worker":
        return dispatch_worker(
            bundle, load_document(args.task), args.role, args.brief, args.host, args.dry_run,
            args.required_family, args.task_dir or args.task.parent, args.task.parent,
        )
    if args.command == "self-test":
        return _print_decision(self_test(args.root))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
