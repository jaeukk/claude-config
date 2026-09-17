"""Policy enforcement and host dispatch for the local multi-agent installation."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import accounts  # sibling module: team-first account selection


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
#: A ``--out`` result shorter than this (after stripping whitespace) is treated as a
#: failed attempt rather than published. A worker that answers a document brief with a
#: one-word refusal exits 0 with a well-formed envelope, so nothing upstream catches it
#: -- and the destination may be a real note the refusal would overwrite. Lower it with
#: ``--min-bytes`` when a short result is the expected output; ``0`` disables the gate.
MIN_PUBLISH_BYTES = 200

CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".m", ".php", ".py", ".rb", ".rs", ".scala", ".sh", ".swift", ".ts", ".tsx",
}
#: Engine-generated dispatch identifier. Nothing outside the engine can produce one, which
#: is what keeps a flag, a brief field, or a worker's own output from steering a state path.
DISPATCH_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
#: The engine state layouts written so far, keyed by kind. A kind that is absent is refused
#: rather than created, so the set of engine-owned destinations stays enumerable.
STATE_DIRECTORIES: dict[str, tuple[str, ...]] = {"outputs": ("outputs",), "writes": ("writes",)}
#: Root of this multiagent installation, taken from the running file rather than from
#: ``--root``: the reserved list protects the code that is actually executing.
INSTALLATION_ROOT = Path(__file__).resolve().parents[1]
#: Engine state directly inside a task directory, reserved against every writer except the
#: engine's own state writes.
RESERVED_TASK_FILES = frozenset({
    "task.yaml", "lease.json", "lease.lock", "events.ndjson", OBSERVED_AUTHOR_FILE,
})
#: Engine state trees inside a task directory.
RESERVED_TASK_TREES = frozenset({"patches", "outputs", "reviews", "applies", "writes"})
#: A ``--write`` baseline copy is bounded. An unbounded one turns a mistyped destination into a
#: filesystem-filling copy, and a baseline that cannot be captured completely is worthless as a
#: recovery point -- so exceeding either limit refuses the dispatch rather than proceeding
#: without a way back.
WRITE_BASELINE_MAX_FILES = 500
WRITE_BASELINE_MAX_BYTES = 50 * 1024 * 1024
#: Denied beneath a directory target. For a directory these are not defense in depth: they *are*
#: the boundary for everything the pre-launch check cannot see, because a path that does not exist
#: yet cannot be vetted before the worker creates it. `.git*` is spelled out rather than globbed
#: because `.github` and `.gitignore` are siblings a `.git/**` pattern would miss.
#: Denied beneath a directory target, relative to it. For a directory these are not defense in
#: depth: they *are* the boundary for everything the pre-launch check cannot see, because a path
#: that does not exist yet cannot be vetted before the worker creates it. Each entry is emitted
#: twice -- once at the target root and once under `**/` -- because whether `**/x` also matches a
#: root-level `x` is a property of the CLI's matcher that this engine should not have to assume.
#: `.git*` is a prefix, matching `_reserved_reason`; an enumeration would miss whatever
#: `.git`-prefixed file appears next.
RESERVED_WRITE_DENY = (
    ".git*", ".git*/**", ".claude", ".claude/**", ".codex", ".codex/**",
    ".vscode", ".vscode/**", ".mcp.json",
    # Engine state. A directory target above a task tree would otherwise let a worker rewrite
    # the contract, the lease, or the records of its own dispatch.
    "task.yaml", "lease.json", "lease.lock", "events.ndjson", "observed-author.json",
    ".active-task", "outputs/**", "writes/**", "patches/**", "reviews/**", "applies/**",
)
#: Installation trees whose contents decide what the engine does next.
RESERVED_INSTALLATION_TREES = frozenset({"engine", "policy", ".claude", "_shared"})
#: Path components that would change what runs later, wherever in a path they appear.
RESERVED_COMPONENTS = frozenset({".claude", ".codex", ".vscode", ".mcp.json"})


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
        account = backend.get("account")
        config_dir = backend.get("config_dir")
        if account is not None and account not in {"private", "team"}:
            errors.append(f"backend {alias} has unknown account {account!r}")
        if config_dir is not None and (not isinstance(config_dir, str) or not config_dir.strip()):
            errors.append(f"backend {alias} config_dir must be a non-empty string")
        if (account is not None or config_dir is not None) and backend.get("host") != "claude-code":
            errors.append(f"backend {alias} binds an account but is not a claude-code host")
        if account == "team" and not config_dir:
            errors.append(f"backend {alias} is team-bound but has no config_dir")

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
    exclude_account_bound: bool = False,
    availability: dict[str, bool] | None = None,
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
    exclude_account_bound:
        Skip backends bound to a non-private account. Native Task-tool children
        and Orca launches run under the session's own login and cannot switch.
    availability:
        Dispatch-time snapshot ``{account: available}``; a backend whose account
        is marked unavailable is skipped. ``None`` assumes every account is
        available (validation and dry runs stay offline).
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
        account = backend.get("account")
        if account not in (None, "private"):
            if exclude_account_bound:
                continue
            if availability is not None and not availability.get(account, True):
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
    if "read_scope" in task:
        readable = resolve_read_scope(task["read_scope"])
        if not readable.allowed:
            errors.append(f"read_scope: {readable.reason}")
    if not isinstance(task.get("write_scope"), list):
        errors.append("write_scope must be a list")
    else:
        for scope in task["write_scope"]:
            if "*" in _scope_root(str(scope)):
                errors.append(
                    f"write_scope entry {scope!r} contains a glob that is not a trailing "
                    "/** or /*; scope entries are directory prefixes and any other "
                    "wildcard would match nothing"
                )
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


def _scope_root(scope: str) -> str:
    """Return the directory prefix a ``write_scope`` entry names.

    ``"src/**"`` is the conventional spelling for "everything under ``src``", and the
    bare directory form already means exactly that here, so both resolve to the same
    prefix. Only a *trailing* glob is accepted: a ``*`` anywhere else is rejected by
    ``validate_task``, because resolving it as a literal directory name would match
    nothing while looking like it matched everything.
    """
    parts = str(scope).replace("\\", "/").rstrip("/").split("/")
    while parts and parts[-1] in {"*", "**"}:
        parts.pop()
    return "/".join(parts)


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
        scope_path = Path(_scope_root(scope))
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
        return resolve_binding(
            bundle, role, author_family=author_family, conductor_host=host,
            exclude_account_bound=bool(action.get("native")),
        )

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
        # Extend only. A dispatch heartbeats with its own worker TTL, which is shorter
        # than the TTL a conductor acquires for the whole task; writing it in would cut
        # the lease down mid-task and strand the next dispatch with "a live matching
        # lease is required".
        payload["expires_epoch"] = max(
            float(payload.get("expires_epoch", 0)), time.time() + ttl_seconds
        )
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
    """Append an event only when the caller owns the live task lease.

    The conductor's ``append-event`` subcommand, unchanged in what it accepts and what it
    returns. The destination goes through ``_state_file`` because ``events.ndjson`` is
    engine state like any other record: appending through a symlink planted in its place
    would write the task's history somewhere nobody inspects.

    A write made *during a dispatch* wants more than this -- the lease lock, the captured
    generation, and a revalidated contract -- and uses ``_append_state_event`` instead.
    """
    lease_path = task_dir / "lease.json"
    if not lease_path.exists():
        return Decision(False, "task has no lease")
    lease = load_document(lease_path)
    if lease.get("owner") != owner or float(lease.get("expires_epoch", 0)) <= time.time():
        return Decision(False, "a live matching lease is required")
    try:
        destination = _state_file(task_dir, "events.ndjson")
    except (OSError, ValueError) as error:
        return Decision(False, f"event destination refused: {error}")
    record = {"at": utc_now(), "owner": owner, **event}
    with destination.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return Decision(True, "event appended")


# ── guards: one lease check, two destination checks ──────────────────────────
#
# Every engine write runs the lease check and exactly one destination check. The two
# destination checks are alternatives and neither contains the other: engine state is
# reached only through `_state_path`, which no caller can influence, while anything landing
# in `target_repo` goes through `authorize_action` plus `resolve_publication_path`. State
# writes are therefore not scope-checked; the reserved list exists to keep every *other*
# writer off the same paths.


def _lease_check(
    bundle: PolicyBundle,
    task_dir: Path,
    owner: str,
    generation: str | None,
    contract_path: Path,
) -> tuple[Decision, dict[str, Any] | None]:
    """Re-establish, under the lease lock, that this operation may still write.

    Run before *every* engine write rather than once per operation. A lease that lapsed
    and was reacquired mid-run means another conductor owns the task now, and the
    generation captured when this dispatch claimed its slot no longer matches -- so an
    operation that loses its lease halfway stops at its next write instead of finishing
    against someone else's task. The contract is reloaded from disk rather than reused
    from memory, so a ``write_scope`` narrowed during the run is honored by the write that
    follows it and not by the one that preceded it.

    The owner is checked against **both** the operation's own owner and the reloaded
    contract's ``conductor.lease_owner``. Either alone leaves a gap: comparing only the
    operation's owner lets a contract that was rewritten to name a different conductor
    keep being written to under the old identity, and comparing only the contract's owner
    lets whoever can edit the contract adopt a lease they never took.

    Everything the decision rests on is read inside ``_lease_lock``. Validating after
    releasing it would decide on a contract that another process may already have
    replaced -- the window is small, but it is exactly the window this check exists to
    close.

    Parameters
    ----------
    generation:
        The operation's captured lease generation (``acquired_at``).
    contract_path:
        The task contract file, reread here.

    Returns
    -------
    tuple
        The decision and, only when it allows, the freshly loaded contract.
    """
    lease_path = task_dir / "lease.json"
    try:
        with _lease_lock(task_dir):
            if not lease_path.exists():
                return Decision(False, "task has no lease"), None
            lease = load_document(lease_path)
            if lease.get("owner") != owner:
                return Decision(False, "lease owner mismatch"), None
            if float(lease.get("expires_epoch", 0)) <= time.time():
                return Decision(False, "task lease has expired"), None
            if lease.get("acquired_at") != generation:
                return Decision(False, "lease was reacquired since this operation started"), None
            contract = load_document(contract_path)
            declared = contract.get("conductor", {}).get("lease_owner")
            if declared != owner:
                return Decision(
                    False,
                    f"contract declares conductor.lease_owner={declared!r} but this "
                    f"operation holds the lease as {owner!r}",
                ), None
            errors, _ = validate_task(bundle, contract)
            if errors:
                return Decision(False, f"invalid task contract: {errors[0]}"), None
    except TimeoutError as error:
        return Decision(False, str(error)), None
    except (OSError, ValueError) as error:
        return Decision(False, f"task contract could not be reloaded: {error}"), None
    return Decision(True, "lease is live and the contract still validates"), contract


def _verified_state_directory(task_dir: Path, components: tuple[str, ...]) -> Path:
    """Return ``task_dir`` joined with ``components``, refusing any symlinked component.

    The walk starts at the filesystem root, not at ``task_dir``. A symlinked *task
    directory* is the same attack as a symlinked ``outputs/`` one level down -- the engine
    would write its own records somewhere it never inspected -- and checking only the
    components the engine appends would miss it entirely.

    The consequence is a real deployment constraint, stated here rather than discovered
    later: the installation and its task directories must not sit behind a symlinked path.
    A path that does raises instead of silently writing through the link.
    """
    absolute = Path(os.path.abspath(task_dir))
    current = Path(absolute.anchor)
    for component in (*absolute.parts[1:], *components):
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{current} is a symlink; engine state must be a real directory")
    # Created only after the whole chain is known to be link-free, and only below
    # ``task_dir``: the engine never conjures a task directory into existence.
    directory = absolute
    for component in components:
        directory = directory / component
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{directory} is not a real directory")
    return directory


def _verified_state_leaf(directory: Path, name: str) -> Path:
    """Return ``directory/name``, refusing a symlink or a name that leaves the directory.

    Both checks matter and neither implies the other. The ``lstat`` catches a state file
    replaced by a symlink -- comparing two *resolved* paths never would, because both
    sides resolve to the link's target and so always agree. Comparing the resolved parent
    catches the opposite mistake: a name that tries to climb out of the directory.
    """
    candidate = directory / name
    if candidate.is_symlink():
        raise ValueError(f"{candidate} is a symlink; engine state must be a real file")
    if candidate.resolve(strict=False).parent != Path(os.path.realpath(directory)):
        raise ValueError(f"{candidate} does not resolve inside {directory}")
    return candidate


def _state_path(task_dir: Path, kind: str, dispatch_id: str, ext: str) -> Path:
    """Return the engine-owned path for one state file, creating its directory.

    The destination is derived entirely from ``kind`` and an engine-generated
    ``dispatch_id``; no CLI flag, brief field, or worker output reaches any part of it.
    """
    relative = STATE_DIRECTORIES.get(kind)
    if relative is None:
        raise ValueError(f"no engine state layout for kind {kind!r}")
    if not DISPATCH_ID_PATTERN.match(dispatch_id):
        raise ValueError(f"dispatch id is not engine-generated: {dispatch_id!r}")
    return _verified_state_leaf(
        _verified_state_directory(task_dir, relative), f"{dispatch_id}{ext}"
    )


def _state_file(task_dir: Path, name: str) -> Path:
    """Return the engine-owned path for one fixed-name state file in a task directory.

    The fixed-name counterpart of ``_state_path``: ``events.ndjson`` and
    ``observed-author.json`` are named by the layout itself rather than by a dispatch, so
    there is no identifier to validate -- but they need the same protection against a
    symlink standing where the engine expects a real file.
    """
    if name not in RESERVED_TASK_FILES:
        raise ValueError(f"{name!r} is not a fixed engine state file")
    return _verified_state_leaf(_verified_state_directory(task_dir, ()), name)


def _task_state_reason(task_dir: Path, destination: Path) -> str | None:
    """Return why ``destination`` is engine state of the task in ``task_dir``, or ``None``.

    Reserves by **identity**, not by how a path happens to be spelled. Anything that
    compares a destination against a ``tasks/`` prefix relative to ``target_repo`` can be
    walked around by moving the boundary: point ``target_repo`` at the installation's
    parent and the prefix becomes ``multiagent/tasks/``; point it at the task directory
    itself and the prefix disappears entirely. Both spellings name the same bytes on disk,
    so the comparison has to be made against the real directory.
    """
    try:
        inside = destination.relative_to(task_dir).parts
    except ValueError:
        return None
    if not inside:
        return f"{task_dir} is a task directory"
    if inside[0] in RESERVED_TASK_TREES:
        return f"{inside[0]}/ is an engine state tree of the task in {task_dir}"
    if len(inside) == 1 and inside[0] in RESERVED_TASK_FILES:
        return f"{inside[0]} is engine state of the task in {task_dir}"
    return None


def _reserved_reason(destination: Path, relative: Path, task_dir: Path | None) -> str | None:
    """Return why a destination is reserved, or ``None`` if it is not.

    A denylist, and knowingly so: a new kind of executable configuration is unprotected
    until it is added here, which is why this function and its tests are the one place to
    extend.

    Engine state is reserved three ways over, because one of them alone is escapable.
    Every task directory of *this installation* is reserved by its real location, however
    ``target_repo`` is spelled; the dispatch's own ``task_dir`` is reserved by identity,
    for an installation whose tasks live somewhere else entirely; and the original
    ``tasks/<id>/`` prefix relative to ``target_repo`` still applies, which keeps an
    unrelated repository's task state protected too.

    Parameters
    ----------
    destination:
        The absolute destination.
    relative:
        The destination inside ``target_repo``.
    task_dir:
        The dispatching operation's own task directory, if there is one.
    """
    parts = relative.parts
    for component in parts:
        # Every `.git*` name in one rule: `.git/`, `.gitattributes`, `.gitmodules`,
        # `.gitignore`, a submodule's `sub/.git`, and `.github/` -- which holds workflows
        # that run on push. The rule is deliberately the prefix rather than a list of
        # known names, so a `.git`-prefixed name nobody has thought of is covered too.
        if component.startswith(".git"):
            return f"{component} is repository or forge metadata"
        if component in RESERVED_COMPONENTS:
            return f"{component} is tool or execution configuration"
    if parts[:1] == ("tasks",):
        if parts[1:] == (".active-task",):
            return "tasks/.active-task is engine state"
        if len(parts) >= 3 and parts[2] in RESERVED_TASK_TREES:
            return f"tasks/{parts[1]}/{parts[2]}/ is an engine state tree"
        if len(parts) == 3 and parts[2] in RESERVED_TASK_FILES:
            return f"{relative.as_posix()} is engine state of a task"
    installation_tasks = INSTALLATION_ROOT / "tasks"
    try:
        under = destination.relative_to(installation_tasks).parts
    except ValueError:
        under = ()
    if under == (".active-task",):
        return "tasks/.active-task of the multiagent installation is engine state"
    if len(under) >= 2:
        reason = _task_state_reason(installation_tasks / under[0], destination)
        if reason is not None:
            return reason
    if task_dir is not None:
        reason = _task_state_reason(Path(os.path.realpath(task_dir)), destination)
        if reason is not None:
            return reason
    try:
        inside = destination.relative_to(INSTALLATION_ROOT).parts
    except ValueError:
        inside = ()
    if inside[:1] and inside[0] in RESERVED_INSTALLATION_TREES:
        return f"{inside[0]}/ is code or policy of the multiagent installation"
    return None


def resolve_publication_path(
    target_repo: str | Path, path: str, task_dir: Path | None = None
) -> Decision:
    """Resolve a publication destination inside ``target_repo`` and apply the reserved rules.

    Every step is chosen so a path cannot be laundered past the reserved list. ``..`` is
    refused outright rather than normalized: collapsing it lexically is wrong when a
    component is a symlink, and collapsing it by following symlinks is exactly the escape
    being prevented. The components are read off the **raw string**, because ``Path``
    silently drops ``.`` and empty segments -- so a check written against ``Path.parts``
    would quietly not be the rule it claimed to be. Existing ancestors are inspected with
    ``lstat``, because a symlinked parent would otherwise let a destination that looks like
    it is inside the repository land anywhere on the disk. The leaf is checked separately
    -- overwriting an existing regular file is the normal case, while a directory or a
    symlink there is refused. Components that do not exist yet are checked as names, so a
    reserved target is refused *before* the engine would create the path that reaches it.

    Refusal is final: there is no approval override.

    Parameters
    ----------
    task_dir:
        The dispatching operation's task directory, so its own engine state is reserved by
        identity rather than by where ``target_repo`` happens to be pointed.

    Returns
    -------
    Decision
        ``details["path"]`` is the absolute destination when allowed.
    """
    target = Path(target_repo).expanduser().resolve()
    # Read off the raw string: `Path("a/./b")` is `a/b` and `Path("a//b")` is `a/b`, so
    # the segments this rule is about are gone before `parts` is ever consulted.
    segments = str(path).replace("\\", "/").split("/")
    for segment in segments[1:] if segments[:1] == [""] else segments:
        if segment in {"", ".", ".."}:
            return Decision(False, f"publication path may not contain {segment!r}")
    supplied = Path(path)
    destination = supplied if supplied.is_absolute() else target / supplied
    try:
        relative = destination.relative_to(target)
    except ValueError:
        return Decision(False, f"publication destination is outside target_repo ({target})")
    if not relative.parts:
        return Decision(False, "publication destination is target_repo itself")
    current = target
    for component in relative.parts[:-1]:
        current = current / component
        if current.is_symlink():
            return Decision(
                False, f"{current} is a symlink; publication parents must be real directories"
            )
        if current.exists() and not current.is_dir():
            return Decision(False, f"{current} exists and is not a directory")
    if destination.is_symlink():
        return Decision(False, f"{destination} is a symlink")
    if destination.exists() and not destination.is_file():
        return Decision(False, f"{destination} exists and is not a regular file")
    reserved = _reserved_reason(destination, relative, task_dir)
    if reserved is not None:
        return Decision(False, f"publication destination is reserved: {reserved}")
    return Decision(
        True, "publication destination is acceptable", {"path": str(destination)}
    )


def _append_state_event(
    bundle: PolicyBundle,
    task_dir: Path,
    owner: str,
    generation: str | None,
    contract_path: Path,
    event: dict[str, Any],
) -> Decision:
    """Append one event as a fully guarded engine state write.

    ``events.ndjson`` is in the state destination table, so an append made during a
    dispatch is a state write and takes the same lease check as the snapshot and the
    provenance record: the lock, the captured generation, and a revalidated contract.
    ``append_event`` on its own has none of those -- it reads the lease unlocked and would
    happily record an attempt against a task another conductor has since taken over.
    """
    check, _ = _lease_check(bundle, task_dir, owner, generation, contract_path)
    if not check.allowed:
        return check
    return append_event(task_dir, owner, event)


def _create_publication_parents(target_repo: Path, destination: Path) -> None:
    """Create a destination's missing parent directories one verified step at a time.

    ``mkdir(parents=True)`` would build the whole chain before anything is inspected, so a
    component that became a symlink between the check and the creation would go unnoticed.
    Each component is created and then ``lstat``-verified, which is where the reserved
    rules' promise about not-yet-existing parents actually lands.
    """
    current = target_repo
    for component in destination.relative_to(target_repo).parts[:-1]:
        current = current / component
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise OSError(f"{current} is not a real directory")


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
    env:
        Environment overrides for the child (``CLAUDE_CONFIG_DIR`` for an
        account-bound backend). Merged over a copy of the parent environment.
    result_file:
        Engine-derived file the worker's CLI writes its final message to, for hosts
        that offer one. Set by the builder, read and then removed by ``_run_worker``.
        ``None`` means the host has no such channel and the result comes from stdout.
    """

    program: str
    args: list[str]
    enforcement: str
    prompt_via: str = "stdin"
    isolated_cwd: bool = False
    env: dict[str, str] = field(default_factory=dict)
    result_file: Path | None = None


def _codex_cli(
    backend: dict[str, Any], role: str | None = None, capture_result: bool = False
) -> WorkerCommand:
    """Build the Codex worker command, made read-only by its own OS sandbox.

    ``-o`` (``--output-last-message``) is added only when ``capture_result`` says the engine
    is going to consume the result. Codex streams progress around its final message, so
    stdout is not a channel the engine can read a result from without guessing where the
    answer starts -- but a ``text`` dispatch has nothing to read, and the flag carries a
    host-side path that the WSL launcher cannot translate. Adding it unconditionally would
    therefore break every ``--host wsl`` Codex dispatch to buy a file nobody opens.

    The path is engine-derived and unique per dispatch, and the file is deliberately *not*
    created here, so a dry run leaves nothing behind and ``_run_worker`` owns the removal.
    """
    result_file = (
        Path(tempfile.gettempdir()) / f"multiagent-codex-{uuid.uuid4().hex}.txt"
        if capture_result
        else None
    )
    return WorkerCommand(
        "codex",
        [
            "exec", "--sandbox", "read-only", "--model", str(backend["model"]),
            "-c", f'model_reasoning_effort="{backend["effort"]}"',
            *(() if result_file is None else ("-o", str(result_file))),
            "--skip-git-repo-check", "--ephemeral", "--color", "never", "-",
        ],
        "os-sandbox-read-only",
        result_file=result_file,
    )


def _claude_cli(
    backend: dict[str, Any], role: str | None = None, capture_result: bool = False
) -> WorkerCommand:
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
    # ``--output-format json`` gives the result envelope (``is_error``,
    # ``api_error_status``) the dispatcher classifies; only the result text is
    # re-emitted. ``config_dir`` binds the run to one account's credentials.
    # ``capture_result`` is accepted and ignored: the envelope already carries the result,
    # so there is no separate file to ask for.
    env: dict[str, str] = {}
    if backend.get("config_dir"):
        env["CLAUDE_CONFIG_DIR"] = str(Path(str(backend["config_dir"])).expanduser())
    return WorkerCommand(
        "claude",
        [
            # `--restricted` drops user, project and local settings, so the operator's plugins,
            # hooks and permission entries stop leaking into a worker that never asked for
            # them. Measured: it also halves the input tokens of a trivial dispatch. What it
            # takes away -- the global CLAUDE.md -- is replaced deliberately below.
            "--restricted",
            "--tools", "Read,Grep,Glob", "--strict-mcp-config",
            "--append-system-prompt", WORKER_BASELINE_PROMPT,
            "--effort", str(backend["effort"]), "--model", str(backend["model"]), "-p",
            "--output-format", "json",
        ],
        "restricted-tool-surface",
        env=env,
    )


def _agy_cli(
    backend: dict[str, Any], role: str | None = None, capture_result: bool = False
) -> WorkerCommand:
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


def worker_cli_args(
    backend: dict[str, Any], role: str | None = None, capture_result: bool = False
) -> WorkerCommand:
    """Return the launch specification for a resolved backend and role.

    ``capture_result`` says the engine will read this worker's result rather than merely
    stream it, which is what decides whether a host that offers a result file is asked for
    one.
    """
    builder = WORKER_CLI.get(backend["host"])
    if builder is None:
        raise ValueError(f"host {backend['host']!r} has no engine dispatcher")
    return builder(backend, role, capture_result)


def build_worker_command(
    backend: dict[str, Any], host_mode: str, target_repo: Path, role: str | None = None,
    capture_result: bool = False, write_settings: Path | None = None,
    read_roots: list[str] | None = None,
) -> tuple[list[str], WorkerCommand]:
    """Resolve a worker launch specification into a native or WSL argv.

    ``write_settings`` turns on ``--write`` mode for a Claude worker: the file tools join the
    tool surface, ``--restricted`` drops every inherited settings source so the generated file
    is the whole permission authority, and the default permission mode makes that file
    default-deny -- an unmatched path has nothing to approve it and no handler to ask.
    """
    spec = worker_cli_args(backend, role, capture_result)
    if read_roots:
        if spec.program != "claude":
            # Codex's read-only sandbox already reads the whole filesystem (measured), so a
            # read root is a Claude concept. Silently accepting it elsewhere would suggest the
            # engine had narrowed something it never touched.
            raise NotImplementedError(
                f"read_scope has no meaning for {spec.program}: its sandbox governs reads"
            )
        added: list[str] = []
        for root in read_roots:
            added += ["--add-dir", root]
        spec = dataclasses.replace(spec, args=[*added, *spec.args])
    if write_settings is not None:
        if spec.program != "claude":
            raise NotImplementedError(
                f"--write has no implementation for {spec.program}; only the Claude CLI takes a "
                "generated permission file"
            )
        args = list(spec.args)
        args[args.index("Read,Grep,Glob")] = "Read,Grep,Glob,Write,Edit,NotebookEdit"
        spec = dataclasses.replace(
            spec,
            args=["--settings", str(write_settings), *args],
            enforcement="restricted-tool-surface + single-destination write allowlist",
        )
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
        if spec.env:
            raise NotImplementedError(
                f"{spec.program} is account-bound (CLAUDE_CONFIG_DIR) and the WSL launcher "
                "does not forward environment; dispatch it natively from inside WSL"
            )
        if spec.result_file is not None:
            # Only reachable when the engine asked for a result file, which today means
            # `--out`. The path is a host path and only `target_repo` is translated below,
            # so handing it across the boundary would leave the engine reading a file
            # nothing ever wrote -- silently, and looking exactly like a worker that
            # returned nothing. A `text` dispatch has no result file and is unaffected.
            raise NotImplementedError(
                f"{spec.program} must write its result to a host-side file to publish, "
                "which the WSL launcher cannot translate; dispatch it natively from "
                "inside WSL"
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


def observed_author_families(contract_dir: Path) -> list[str]:
    """Return every family recorded as having produced part of this task's artifact.

    Authorship accumulates. A failed Claude ``--write`` over retained Codex work leaves both
    families' output in the artifact, and a record that kept only the last writer would erase
    the Codex contribution -- after which a Codex critic would be picked to review work its own
    family partly wrote. So the sidecar carries a list, and independence is judged against all
    of it.

    Older sidecars carry only ``family``; they read as a single-element list, so a record
    written before this change keeps meaning exactly what it meant.
    """
    sidecar = contract_dir / OBSERVED_AUTHOR_FILE
    if not sidecar.exists():
        return []
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UnreadableAuthorRecord(f"{sidecar} cannot be read: {error}") from error
    if not isinstance(payload, dict):
        raise UnreadableAuthorRecord(f"{sidecar} is not an object")
    recorded = payload.get("families")
    if recorded is None:
        single = payload.get("family")
        recorded = [single] if single is not None else []
    if not isinstance(recorded, list) or not recorded:
        raise UnreadableAuthorRecord(f"{sidecar} records no usable family: {recorded!r}")
    for family in recorded:
        if not isinstance(family, str) or family not in KNOWN_FAMILIES:
            raise UnreadableAuthorRecord(f"{sidecar} records an unknown family: {family!r}")
    return sorted(set(recorded))


def record_contributing_family(contract_dir: Path, family: str, source: str) -> None:
    """Add one family to the authorship record, keeping whoever was already there.

    Read-modify-write, because the point is accumulation: overwriting is what loses the
    earlier contributor. Callers hold the task lock, which is what makes this safe.
    """
    existing: list[str] = []
    try:
        existing = observed_author_families(contract_dir)
    except UnreadableAuthorRecord:
        # A damaged record is not a reason to drop the new fact; the reader still refuses to
        # wave a review through, because the result here is a record it can parse but whose
        # history it cannot vouch for.
        existing = []
    families = sorted(set(existing) | {family})
    _state_file(contract_dir, OBSERVED_AUTHOR_FILE).write_text(
        json.dumps({"family": family, "families": families, "source": source}, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _resolve_with_account(bundle: PolicyBundle, role: str, dry_run: bool, **kwargs: Any) -> tuple[Decision, str]:
    """Resolve a role, probing the team account only when it would actually be used.

    Resolving optimistically first means a role whose winner is a Codex backend never
    touches the network: the probe exists to choose between Claude accounts, not to
    gate dispatch. A probe that cannot answer leaves the optimistic choice standing --
    unknown quota routes to team, and the real run reports the truth if it is wrong.
    """
    binding = resolve_binding(bundle, role, availability=None, **kwargs)
    if dry_run or not binding.allowed or binding.details is None:
        return binding, "skipped (dry-run)" if dry_run else "not resolved"
    backend = binding.details
    if backend.get("account") != "team" or not backend.get("config_dir"):
        return binding, "not a team candidate"
    result = accounts.probe(backend["config_dir"], model=backend.get("model"))
    reason = f"{result.state}: {result.reason}"
    if result.available:
        return binding, reason
    return resolve_binding(bundle, role, availability={"team": False}, **kwargs), reason


def _emit_attempt(attempt: accounts.Attempt) -> None:
    """Write the surviving attempt's output through, result text only when parseable."""
    if attempt.envelope is not None:
        sys.stdout.write(str(attempt.envelope.get("result", "")) + "\n")
    else:
        sys.stdout.write(attempt.stdout)
    sys.stderr.write(attempt.stderr)


def _record_attempt(
    bundle: PolicyBundle, task_dir: Path, owner: str, generation: str | None,
    contract_path: Path, role: str, backend: dict[str, Any], number: int,
    reason: str, attempt: accounts.Attempt, dispatch_id: str,
) -> None:
    """Append one attempt record to the task's events; never fatal.

    ``dispatch_id`` is the same identifier the dispatch's own records are named after, so
    the event stream and the record files can be joined after the fact -- including for a
    dispatch whose fallback attempt is the one that produced the artifact.

    Guarded like every other state write: an attempt is not recorded against a lease
    generation this dispatch no longer holds. Still never fatal -- a dispatch that cannot
    record its attempt warns and continues, because losing the note is better than losing
    the result it describes.
    """
    recorded = _append_state_event(bundle, task_dir, owner, generation, contract_path, {
        "type": "worker_attempt", "dispatch_id": dispatch_id, "role": role,
        "backend": backend["backend"], "attempt": number, "reason": reason,
        **attempt.as_event(),
    })
    if not recorded.allowed:
        print(f"warning: attempt not recorded: {recorded.reason}", file=sys.stderr)


def _out_prelaunch_check(
    bundle: PolicyBundle, task: dict[str, Any], role: str, out_path: str,
    task_dir: Path | None = None,
) -> Decision:
    """Decide whether a ``--out`` destination is publishable, before anything is launched.

    Every refusal here is free: no process has started, so a destination the engine was
    never going to be allowed to write costs no tokens to discover.

    ``--out`` never carries code. Code reaches the repository through a reviewed patch, so
    routing a ``.py`` file through here would sidestep the conductor's own code-file cap.

    The mode conflict is *not* checked here. Whether a dispatch would be ``workspace`` mode
    depends on the resolved backend's host as well as the role, and the backend is not
    known until the binding resolves -- which is still before any process launches, so the
    refusal stays free. Checking the role alone here would refuse an ``implementer`` that
    resolves to Codex, which stays ``text`` mode and may legitimately publish.

    Parameters
    ----------
    task_dir:
        Passed through so the task's own engine state is reserved by identity.

    Returns
    -------
    Decision
        On success, ``details["path"]`` is the absolute destination.
    """
    if Path(out_path).suffix.lower() in CODE_SUFFIXES:
        return Decision(
            False, f"--out may not publish code ({Path(out_path).suffix}); use a reviewed patch"
        )
    resolved = resolve_publication_path(task["target_repo"], out_path, task_dir)
    if not resolved.allowed:
        return resolved
    authorized = authorize_action(
        bundle, task, {"kind": "write", "actor_role": "conductor", "path": out_path}
    )
    if not authorized.allowed:
        return authorized
    return resolved


def resolve_read_scope(entries: Any) -> Decision:
    """Resolve a contract's ``read_scope`` into canonical directories a worker may read.

    This is the first mechanism by which a worker reads outside ``target_repo``, and it has no
    enclosing boundary the way ``write_scope`` does -- ``_is_in_scope`` can reject a write path
    for leaving the repository, while a read root's whole purpose is to be elsewhere. So the
    rules are its own, and they authorize rather than contain: what stops a worker reading
    through a symlink out of an approved root is the CLI resolving each path at read time
    (measured), not anything here.

    Returns
    -------
    Decision
        ``details["roots"]`` is the canonical directory list, in contract order.
    """
    if entries is None:
        return Decision(True, "no read_scope declared", {"roots": []})
    if not isinstance(entries, list):
        return Decision(False, "read_scope must be a list")
    protected = [Path(location).expanduser() for location in PROTECTED_READ_LOCATIONS]
    roots: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            return Decision(False, f"read_scope entry must be a non-empty string: {entry!r}")
        segments = entry.replace("\\", "/").split("/")
        for segment in segments[1:] if segments[:1] == [""] else segments:
            if segment in {"", ".", ".."}:
                return Decision(False, f"read_scope entry may not contain {segment!r}: {entry}")
        candidate = Path(_scope_root(entry)).expanduser()
        if not candidate.is_absolute():
            return Decision(False, f"read_scope entry must be absolute: {entry}")
        resolved = candidate.resolve()
        if not resolved.is_dir():
            return Decision(False, f"read_scope entry is not an existing directory: {entry}")
        if resolved == Path(resolved.anchor) or resolved == Path.home():
            return Decision(False, f"read_scope entry is too broad: {resolved}")
        for location in protected:
            reference = location.resolve() if location.exists() else location
            if resolved == reference or reference in resolved.parents \
                    or resolved in reference.parents:
                return Decision(
                    False,
                    f"read_scope entry {resolved} is, contains, or sits inside {reference}, "
                    "which holds credentials or agent configuration",
                )
        roots.append(str(resolved))
    return Decision(True, f"{len(roots)} read root(s) authorized", {"roots": roots})


def resolve_write_target(
    target_repo: str | Path, path: str, task: dict[str, Any], task_dir: Path | None = None
) -> Decision:
    """Resolve the one destination a ``--write`` worker may write, or refuse.

    ``--write`` authorizes a single file or a single directory, never a scope. That is what
    makes the authorization checkable: the destination is vetted here, once, before anything
    launches, and the generated permission rules name it exactly.

    The type rule is deliberately total, because a path that does not exist yet cannot be
    stat'ed into a type: an existing path is whatever it already is, and a missing path is a
    **file** whose parent must already exist. Nothing here creates a directory, so a worker can
    never write outside the declared destination by way of a parent the engine invented.

    Every refusal the publication path applies applies here too -- raw-string segments, symlinked
    ancestors, reserved installation and task trees -- with one difference: code suffixes are
    allowed, because landing code is the entire point of this mode.

    Returns
    -------
    Decision
        ``details`` carries ``path`` (absolute) and ``kind`` (``file`` or ``directory``).
    """
    target = Path(target_repo).expanduser().resolve()
    segments = str(path).replace("\\", "/").split("/")
    for segment in segments[1:] if segments[:1] == [""] else segments:
        if segment in {"", ".", ".."}:
            return Decision(False, f"write target may not contain {segment!r}")
    supplied = Path(path)
    destination = supplied if supplied.is_absolute() else target / supplied
    try:
        relative = destination.relative_to(target)
    except ValueError:
        return Decision(False, f"write target is outside target_repo ({target})")
    if not relative.parts:
        return Decision(False, "write target is target_repo itself")
    current = target
    for component in relative.parts[:-1]:
        current = current / component
        if current.is_symlink():
            return Decision(False, f"{current} is a symlink; write-target parents must be real")
        if current.exists() and not current.is_dir():
            return Decision(False, f"{current} exists and is not a directory")
    if destination.is_symlink():
        return Decision(False, f"{destination} is a symlink")
    if any(character in str(destination) for character in "*?[]{}!"):
        # The destination becomes a permission *pattern*, and the whole resolved path is
        # interpolated -- so a repository directory named `repo[1]` matters as much as a file
        # named `note*.md`. Refused rather than escaped: there is no tested escaping form, and
        # an untested one fails open.
        return Decision(
            False,
            "write target path may not contain pattern metacharacters (*?[]{}!); this includes "
            f"target_repo itself ({destination})",
        )
    reserved = _reserved_reason(destination, relative, task_dir)
    if reserved is not None:
        return Decision(False, f"write target is reserved: {reserved}")
    if not _is_in_scope(str(destination), task["target_repo"], task["write_scope"]):
        return Decision(False, "write target is outside the contract's write_scope")
    if destination.exists():
        kind = "directory" if destination.is_dir() else "file"
        if kind == "file" and not destination.is_file():
            return Decision(False, f"{destination} is neither a regular file nor a directory")
        if kind == "directory":
            # The leaf check cannot see what a directory already contains. A target above a
            # task tree or an installation tree is refused outright rather than relying on the
            # generated deny rules, which exist for paths that do not exist yet.
            for item in destination.rglob("*"):
                try:
                    inside = item.relative_to(target)
                except ValueError:  # pragma: no cover - rglob stays under destination
                    continue
                contained = _reserved_reason(item, inside, task_dir)
                if contained is not None:
                    return Decision(
                        False, f"write target contains reserved state: {item} ({contained})"
                    )
    else:
        kind = "file"
        parent = destination.parent
        if not parent.is_dir():
            return Decision(
                False,
                f"{parent} does not exist; --write creates no directories, so a new file's "
                "parent must already be there",
            )
    return Decision(True, f"write target accepted ({kind})", {"path": str(destination), "kind": kind})


#: Managed settings merge into a worker's permissions even under ``--restricted``, which is the
#: one documented way the generated allowlist stops being the whole authority. Measured
#: 2026-09-18: user- and project-scope grants are both ignored under ``--restricted`` (a control
#: without the flag applied the same grant), so these paths are the only remaining source.
#: An additional read root may not stand in any relation -- equal, ancestor, or descendant -- to
#: one of these. It is a guard against an obviously wrong entry, not a secret boundary: an
#: ordinary project directory can hold credentials of its own and nothing here detects that.
PROTECTED_READ_LOCATIONS = (
    "~/.claude", "~/.claude-team", "~/.codex", "~/.gemini", "~/.copilot", "~/.orca",
    "~/.agents", "~/.ssh", "~/.gnupg", "~/.config", "~/.aws", "~/.mcp.json", "~/.npmrc",
)

#: What a dispatched worker is told, in place of whatever the operator's profile happened to
#: contain. Composed rather than inherited: under ``--restricted`` a worker no longer picks up
#: user settings, which is how the ponytail coding persona was reaching literature-note work
#: (measured 2026-09-17: a normal worker answers yes to "do your instructions contain a ponytail
#: rule", a restricted one answers no). Everything else the old inheritance carried is either
#: brief-specific -- vault layout, HPC schedulers, Zotero -- or unreachable on a read-only tool
#: surface, so it belongs in the brief that needs it, not here.
WORKER_BASELINE_PROMPT = (
    "You are working for Jaeuk Kim, PhD, a physics postdoc. Write in English with American "
    "spelling, in everything you produce. You are a one-shot worker with no channel to ask "
    "questions: state your assumptions explicitly and surface any uncertainty, conflict, or "
    "missing input in your result rather than guessing silently."
)

MANAGED_SETTINGS_PATHS = (
    Path("/etc/claude-code/managed-settings.json"),
    Path("/usr/local/etc/claude-code/managed-settings.json"),
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
)


def managed_settings_present() -> Path | None:
    """Return a managed-settings file if one exists, else ``None``."""
    for candidate in MANAGED_SETTINGS_PATHS:
        try:
            if candidate.exists():
                return candidate
        except OSError:  # pragma: no cover - unreadable mount
            continue
    return None


def write_permission_settings(destination: str | Path, kind: str) -> dict[str, Any]:
    """Build the settings document that authorizes exactly one destination.

    Every rule is an ``Edit`` rule. Measured 2026-09-18: with an allowlist naming only
    ``Write(...)`` a file creation is **refused**, and with the same path named as ``Edit(...)``
    it succeeds -- the file tools' permission checks match against ``Edit``, so a ``Write`` rule
    authorizes nothing. Emitting one would read like a guard while enforcing nothing, on both
    the allow and the deny side.

    The absolute ``//`` form is used because a single leading slash is *not* the absolute
    spelling: a rule written that way matches nothing and fails open, silently. Deny outranks
    allow, so the reserved patterns hold for paths that do not exist yet and therefore could not
    be checked before launch.
    """
    root = str(Path(destination))
    patterns = [f"//{root}/**", f"//{root}"] if kind == "directory" else [f"//{root}"]
    allow = [f"Edit({pattern})" for pattern in patterns]
    deny: list[str] = []
    if kind == "directory":
        deny = [
            f"Edit(//{root}/{prefix}{pattern})"
            for pattern in RESERVED_WRITE_DENY for prefix in ("", "**/")
        ]
    return {"permissions": {"allow": allow, "deny": deny}}


def _file_digest(path: Path) -> dict[str, Any] | None:
    """Return the recorded identity of one file, or ``None`` when it cannot be read.

    An unreadable file must not raise out of a change-set comparison: the caller turns this
    into an explicit "nobody could tell" outcome, which is a different thing from a crash and
    a very different thing from "nothing changed".
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _write_manifest(destination: Path, kind: str) -> Decision:
    """Record what exists at the destination now, or refuse to guess.

    A baseline that is partial is not a recovery point, so anything that cannot be copied
    faithfully -- a symlink, a device, a socket -- refuses instead of being skipped.
    """
    if kind == "file":
        if not destination.exists():
            return Decision(True, "destination does not exist yet", {"files": {}})
        if destination.stat().st_size > WRITE_BASELINE_MAX_BYTES:
            return Decision(
                False, f"write target exceeds the baseline limit ({WRITE_BASELINE_MAX_BYTES} bytes)"
            )
        digest = _file_digest(destination)
        if digest is None:
            return Decision(False, f"{destination} could not be read")
        return Decision(True, "baseline recorded", {"files": {"": digest}})
    files: dict[str, Any] = {}
    total = 0
    for item in sorted(destination.rglob("*")):
        if item.is_symlink():
            return Decision(False, f"{item} is a symlink; a write target may not contain one")
        if item.is_dir():
            continue
        if not item.is_file():
            return Decision(False, f"{item} is not a regular file")
        digest = _file_digest(item)
        if digest is None:
            return Decision(False, f"{item} could not be read")
        files[str(item.relative_to(destination))] = digest
        total += digest["bytes"]
        if len(files) > WRITE_BASELINE_MAX_FILES or total > WRITE_BASELINE_MAX_BYTES:
            return Decision(
                False,
                f"write target exceeds the baseline limit "
                f"({WRITE_BASELINE_MAX_FILES} files, {WRITE_BASELINE_MAX_BYTES} bytes)",
            )
    return Decision(True, "baseline recorded", {"files": files})


def capture_write_baseline(
    task_dir: Path, dispatch_id: str, destination: Path, kind: str
) -> Decision:
    """Copy the destination aside so the change is reversible, and record its manifest."""
    manifest = _write_manifest(destination, kind)
    if not manifest.allowed:
        return manifest
    baseline = _state_path(task_dir, "writes", dispatch_id, ".before")
    if destination.exists():
        if kind == "file":
            shutil.copy2(destination, baseline)
        else:
            shutil.copytree(destination, baseline, symlinks=False)
        # A copy nobody checked is not a recovery point. Re-hash what landed and compare it
        # to the manifest, so a truncated or failed copy refuses here rather than at the
        # restore that needed it.
        copied = _write_manifest(baseline, kind)
        if not copied.allowed:
            return Decision(False, f"baseline copy unreadable: {copied.reason}")
        if (copied.details or {}).get("files") != (manifest.details or {}).get("files"):
            return Decision(False, "baseline copy does not match the destination it copied")
    return Decision(True, "baseline captured", {
        "baseline": str(baseline), "kind": kind, "existed": destination.exists(),
        **(manifest.details or {}),
    })


def write_change_set(baseline: dict[str, Any], destination: Path, kind: str) -> dict[str, Any]:
    """Compare the destination against its baseline manifest.

    Unreadable state is reported as ``inspection_failed`` rather than as an empty change set:
    "nothing changed" and "nobody could tell" must never look alike.
    """
    before = dict(baseline.get("files", {}))
    now = _write_manifest(destination, kind)
    if not now.allowed:
        return {"inspection_failed": now.reason}
    after = dict((now.details or {}).get("files", {}))
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(
        name for name in set(before) & set(after) if before[name]["sha256"] != after[name]["sha256"]
    )
    return {
        "created": created, "modified": modified, "removed": removed,
        "unchanged": sorted(set(before) & set(after) - set(modified)),
    }


def changed_anything(change_set: dict[str, Any]) -> bool:
    """Whether a change set records a real change, treating an unreadable one as yes."""
    if "inspection_failed" in change_set:
        return True
    return bool(change_set["created"] or change_set["modified"] or change_set["removed"])


def restore_write(task_dir: Path, dispatch_id: str) -> Decision:
    """Put a ``--write`` destination back to its recorded baseline.

    Refuses when the destination no longer matches what was recorded *after* the run: something
    else has touched it since, and replacing it blindly would destroy that work rather than the
    worker's. The comparison and the replacement run under the task's lock, because a check
    followed by an unlocked delete is exactly the window in which a concurrent writer's work
    disappears between the two.

    The lock binds cooperating engine operations on this task. It does not stop an editor or a
    hand-run command, so the guarantee is "no other dispatch or restore interleaves", not
    "nobody else can touch the file".
    """
    with _lease_lock(task_dir):
        return _restore_write_locked(task_dir, dispatch_id)


def _restore_write_locked(task_dir: Path, dispatch_id: str) -> Decision:
    """The body of :func:`restore_write`, run with the task lock held."""
    record_path = _state_path(task_dir, "outputs", dispatch_id, ".json")
    if not record_path.exists():
        return Decision(False, f"no dispatch record at {record_path}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    baseline = record.get("baseline")
    if not baseline:
        return Decision(False, "this dispatch recorded no write baseline")
    destination = Path(record["destination"])
    kind = baseline["kind"]
    current = write_change_set({"files": record.get("after", {})}, destination, kind)
    if changed_anything(current):
        return Decision(False, f"{destination} changed after the run was recorded; not restoring")
    for parent in destination.parents:
        if parent == parent.parent:
            break
        if parent.is_symlink():
            # Content matched, but through a redirected path: the comparison above says
            # nothing about *where* these bytes live, so unlinking would act on the target of
            # the link rather than on what was recorded.
            return Decision(False, f"{parent} is now a symlink; not restoring through it")
    stored = Path(baseline["baseline"])
    if baseline.get("existed"):
        # Verify the copy before destroying anything. A corrupt baseline discovered *after*
        # the destination is gone leaves nothing at all.
        recorded = _write_manifest(stored, kind)
        if not recorded.allowed:
            return Decision(False, f"baseline unreadable: {recorded.reason}")
        if (recorded.details or {}).get("files") != baseline.get("files"):
            return Decision(False, "baseline no longer matches what was recorded; not restoring")
        # Staged beside the destination, not beside the baseline: a rename across filesystems
        # fails with EXDEV, and it would fail *after* the destination was already removed.
        staged = destination.with_name(destination.name + ".restoring")
        if staged.exists():
            shutil.rmtree(staged) if staged.is_dir() else staged.unlink()
        shutil.copytree(stored, staged) if kind == "directory" else shutil.copy2(stored, staged)
    if destination.exists():
        shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
    if baseline.get("existed"):
        staged.replace(destination)
    return Decision(True, f"restored {destination} to its pre-dispatch baseline")


def _commit_out(
    bundle: PolicyBundle,
    task_dir: Path,
    contract_path: Path,
    owner: str,
    generation: str | None,
    dispatch_id: str,
    out_path: str,
    provenance: dict[str, Any],
    text: str | None,
) -> Decision:
    """Publish one ``--out`` result and its provenance record, or record the failure.

    The order is the point. The immutable snapshot is written first, then the destination,
    then the record that names both -- so an interruption leaves a snapshot nobody points
    at rather than a record naming a snapshot that was never written, and a destination
    overwritten later still has the reviewable bytes beside the task. ``text`` is ``None``
    for an attempt that did not satisfy the success predicate: that path writes the record
    and nothing else, which is what "a failed attempt writes nothing" means.

    Both destination checks are re-run here against the contract as reloaded by the lease
    check, not against the copy the pre-launch check saw. A ``write_scope`` narrowed while
    the worker was running must refuse the publication it no longer covers.
    """
    record = {"dispatch_id": dispatch_id, "at": utc_now(),
              "lease_generation": generation, **provenance}

    def leased() -> tuple[Decision, dict[str, Any] | None]:
        return _lease_check(bundle, task_dir, owner, generation, contract_path)

    try:
        if text is not None:
            data = text.encode("utf-8")
            check, _ = leased()
            if not check.allowed:
                return Decision(False, f"snapshot refused: {check.reason}")
            snapshot = _state_path(task_dir, "outputs", dispatch_id, ".snapshot")
            snapshot.write_bytes(data)
            record.update({
                "snapshot": str(snapshot),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            })

            check, contract = leased()
            if not check.allowed or contract is None:
                return Decision(False, f"publication refused: {check.reason}")
            authorized = authorize_action(
                bundle, contract, {"kind": "write", "actor_role": "conductor", "path": out_path}
            )
            if not authorized.allowed:
                return Decision(False, f"publication refused: {authorized.reason}")
            resolved = resolve_publication_path(contract["target_repo"], out_path, task_dir)
            if not resolved.allowed:
                return Decision(False, f"publication refused: {resolved.reason}")
            target = Path(contract["target_repo"]).expanduser().resolve()
            destination = Path((resolved.details or {})["path"])
            _create_publication_parents(target, destination)
            # Temporary file in the already-checked directory, then rename over the
            # already-checked destination: neither is engine state nor a publication of
            # its own, so neither takes a destination check.
            temporary = destination.with_name(f"{destination.name}.{dispatch_id}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
            record["destination"] = str(destination)

        check, _ = leased()
        if not check.allowed:
            return Decision(False, f"provenance record refused: {check.reason}")
        _state_path(task_dir, "outputs", dispatch_id, ".json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as error:
        # A symlink that appeared mid-flight, a full disk, or a path the engine refuses to
        # derive: all of them stop the publication and are reported, never half-applied.
        return Decision(False, f"publication failed: {error}")
    succeeded = record.get("status") == "succeeded"
    return Decision(
        succeeded,
        f"--out {record['status']}" if not succeeded else f"published to {record['destination']}",
        record,
    )


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
    out_path: str | None = None,
    contract_path: Path | None = None,
    min_publish_bytes: int = MIN_PUBLISH_BYTES,
    write_path: str | None = None,
) -> int:
    """Dispatch one bounded worker as a subprocess.

    The backend is whichever the binding resolves under the conducting host, so
    a Codex conductor reaches a Claude critic through the ``claude`` CLI exactly
    as a Claude conductor reaches a Codex one -- neither host's native child API
    is involved.

    A real dispatch holds a worker slot on the task lease for its duration, so
    ``task_dir`` must contain a live lease owned by ``conductor.lease_owner``. A
    dry run resolves and prints without claiming anything.

    ``out_path`` turns on publication: the worker's surface is unchanged -- it stays
    read-only and never learns the destination -- and the engine writes the text it
    returned, once, after checking that it may. Publication needs ``contract_path`` so the
    guards can reload the contract from disk rather than trust the copy in memory.
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
    if not dry_run and contract_path is None:
        # Every state write reloads and revalidates the contract from disk, so a real
        # dispatch cannot proceed without knowing which file that is. Guessing the name
        # would be worse than refusing: the guard would silently check the wrong file.
        print(json.dumps(Decision(
            False, "dispatch needs the contract path so every state write can recheck it"
        ).as_dict(), indent=2), file=sys.stderr)
        return 2
    read_scope = resolve_read_scope(task.get("read_scope"))
    if not read_scope.allowed:
        print(json.dumps(read_scope.as_dict(), indent=2), file=sys.stderr)
        return 2
    read_roots: list[str] = (read_scope.details or {})["roots"]
    if read_roots and host_mode != "native":
        print(json.dumps(Decision(
            False, f"read_scope is not implemented for --host {host_mode}: the launcher "
                   "translates only target_repo, so these paths would not resolve"
        ).as_dict(), indent=2), file=sys.stderr)
        return 2
    destination: Path | None = None
    write_target: Path | None = None
    write_kind: str | None = None
    if write_path is not None:
        # Both modes land a result; letting them run together would mean two authorities over
        # what the dispatch produced, with no rule for which wins.
        if out_path is not None:
            print(json.dumps(Decision(
                False, "--write and --out cannot be combined: a dispatch lands its result one way"
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        if host_mode != "native":
            print(json.dumps(Decision(
                False, f"--write is not implemented for --host {host_mode}: the generated "
                       "permission file's path does not translate"
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        if not bundle.roles.get(role, {}).get("may_write"):
            print(json.dumps(Decision(
                False, f"role {role} may not write; --write is refused"
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        managed = managed_settings_present()
        if managed is not None:
            # The generated allowlist is the boundary only while it is the whole authority.
            # A managed file merges into it, so the engine refuses rather than enforcing
            # something it cannot describe.
            print(json.dumps(Decision(
                False,
                f"--write refused: managed settings at {managed} merge into the worker's "
                "permissions, so the generated allowlist would not be the whole authority",
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        resolved_write = resolve_write_target(task["target_repo"], write_path, task, task_dir)
        if not resolved_write.allowed:
            print(json.dumps(resolved_write.as_dict(), indent=2), file=sys.stderr)
            return 2
        write_target = Path((resolved_write.details or {})["path"])
        write_kind = (resolved_write.details or {})["kind"]
    if out_path is not None:
        acceptable = _out_prelaunch_check(bundle, task, role, out_path, task_dir)
        if not acceptable.allowed:
            print(json.dumps(acceptable.as_dict(), indent=2), file=sys.stderr)
            return 2
        destination = Path((acceptable.details or {})["path"])
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
    contributors: list[str] = []
    if role in {"critic", "verifier"} and contract_dir is not None:
        try:
            contributors = observed_author_families(contract_dir)
        except UnreadableAuthorRecord as error:
            print(json.dumps(Decision(
                False, f"{role} blocked: {error}"
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        if len(contributors) > 1:
            # Two families' output in one artifact. Every candidate reviewer shares a family
            # with part of what it would be reviewing, so there is no independent reviewer to
            # pick -- and picking one anyway is exactly the silent failure this check exists
            # to prevent. Split the artifact or review it by hand.
            print(json.dumps(Decision(
                False,
                f"{role} blocked: this artifact has mixed authorship ("
                f"{', '.join(contributors)}), so no candidate is independent of all of it",
            ).as_dict(), indent=2), file=sys.stderr)
            return 2
        seen = contributors[0] if contributors else None
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
    binding, probe_reason = _resolve_with_account(
        bundle,
        role,
        dry_run,
        author_family=task.get("author_family"),
        required_family=required_family,
        conductor_host=task["conductor"]["host"],
    )
    if not binding.allowed or binding.details is None:
        print(json.dumps(binding.as_dict(), indent=2), file=sys.stderr)
        return 2
    backend = binding.details
    target_repo = Path(task["target_repo"]).resolve()
    write_settings_path: Path | None = None
    baseline: dict[str, Any] | None = None
    command, spec = build_worker_command(
        backend, host_mode, target_repo, role, capture_result=out_path is not None,
        write_settings=write_settings_path, read_roots=read_roots,
    )
    if dry_run:
        print(json.dumps(
            {
                "command": command,
                "cwd": "<isolated temporary directory>" if spec.isolated_cwd else str(target_repo),
                "backend": backend["host"],
                "enforcement": spec.enforcement,
                "prompt_via": spec.prompt_via,
                "account": backend.get("account", "private"),
                "config_dir": spec.env.get("CLAUDE_CONFIG_DIR"),
                "probe": probe_reason,
                "out": None if destination is None else str(destination),
                "read_roots": read_roots,
                # Named in the preview because the real command differs: write mode adds
                # `--restricted`, the file tools, and a generated permission file. A dry run
                # that showed the read-only argv would preview something that never runs.
                "write": None if write_target is None else {
                    "destination": str(write_target), "kind": write_kind,
                    "enforcement": "restricted-tool-surface + single-destination write allowlist",
                },
            },
            indent=2,
        ))
        return 0
    # One identifier per dispatch, engine-generated: it names this dispatch's records and
    # ties them to the attempt events, and nothing outside the engine can choose it.
    dispatch_id = uuid.uuid4().hex
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
        if write_target is not None and write_kind is not None:
            # Inside the try, so a refusal releases the slot through the same `finally` as
            # every other exit. Both of these name engine state by the dispatch id, which
            # exists only once the dispatch is real -- hence after the claim, not before.
            captured = capture_write_baseline(task_dir, dispatch_id, write_target, write_kind)
            if not captured.allowed:
                print(json.dumps(captured.as_dict(), indent=2), file=sys.stderr)
                return 2
            baseline = captured.details
            write_settings_path = _state_path(task_dir, "writes", dispatch_id, ".settings.json")
            write_settings_path.write_text(
                json.dumps(write_permission_settings(write_target, write_kind), indent=2),
                encoding="utf-8",
            )
            command, spec = build_worker_command(
                backend, host_mode, target_repo, role, capture_result=False,
                write_settings=write_settings_path, read_roots=read_roots,
            )
        attempt = _run_worker(
            command, spec, task, role, brief_path, target_repo, backend,
            None if write_target is None else str(write_target),
        )
        _record_attempt(
            bundle, slots, owner, generation, contract_path, role, backend, 1,
            probe_reason, attempt, dispatch_id,
        )
        attempt_number = 1
        if attempt.classification == "rate_limited" and backend.get("account") == "team":
            # Exactly one private retry, and only here: CLI Claude workers are
            # read-only by construction (``--tools Read,Grep,Glob``), so replaying
            # the same brief duplicates computation, never side effects.
            accounts.note_run_rate_limited(backend["config_dir"])
            if write_target is not None:
                # The retry below exists because a repeated brief duplicates computation and
                # never side effects. A write attempt may already have changed files, so the
                # premise is gone and the attempt is reported instead of repeated.
                print(json.dumps(Decision(
                    False, "rate-limited write attempt is not retried; inspect the change set"
                ).as_dict(), indent=2), file=sys.stderr)
                fallback = Decision(False, "no retry in write mode")
            else:
                fallback = resolve_binding(
                    bundle, role, author_family=task.get("author_family"),
                    required_family=required_family, conductor_host=task["conductor"]["host"],
                    availability={"team": False},
                )
            if fallback.allowed and fallback.details is not None \
                    and fallback.details["backend"] != backend["backend"]:
                backend = fallback.details
                command, spec = build_worker_command(
                    backend, host_mode, target_repo, role, capture_result=out_path is not None,
                    write_settings=write_settings_path, read_roots=read_roots,
                )
                attempt = _run_worker(command, spec, task, role, brief_path, target_repo, backend)
                _record_attempt(
                    bundle, slots, owner, generation, contract_path, role, backend, 2,
                    "fallback after team rate limit", attempt, dispatch_id,
                )
                attempt_number = 2
        _emit_attempt(attempt)
        status = attempt.exit_code
        if out_path is not None and destination is not None:
            # The predicate, not the exit code: a Claude run can exit 0 having produced no
            # readable envelope, and a Codex run can exit 0 with an empty result file.
            # Publishing either would write an empty document over a real one.
            failure = accounts.attempt_failure_reason(attempt, backend["host"])
            if failure is None and min_publish_bytes > 0:
                body = (attempt.result_text or "").strip()
                if len(body.encode("utf-8")) < min_publish_bytes:
                    failure = "below_min_bytes"
            succeeded = failure is None
            committed = _commit_out(
                bundle, slots, contract_path, owner, generation, dispatch_id, out_path,
                {
                    "status": "succeeded" if succeeded else "failed",
                    # Named, not merely "failed": `malformed_envelope` is the failure that
                    # looks like success from the outside, and a record that hid it would
                    # send someone hunting for an exit code that was 0 all along.
                    "failure_reason": failure,
                    "destination": str(destination),
                    "role": role,
                    "backend": backend["backend"],
                    "family": backend["family"],
                    "account": backend.get("account", "private"),
                    "model": backend.get("model"),
                    "attempt": attempt_number,
                },
                attempt.result_text if succeeded else None,
            )
            print(json.dumps(committed.as_dict(), indent=2), file=sys.stderr)
            # Non-zero whenever nothing landed, including the exit-0-but-unusable case:
            # the conductor must not read a clean exit as "the file is there".
            if not committed.allowed:
                return 2
        if write_target is not None and write_kind is not None and baseline is not None:
            # The worker wrote -- or did not -- and only the filesystem knows which. A clean
            # exit with an empty change set is a real outcome, and so is a failed run that
            # changed files; neither is what an exit code alone would say.
            changes = write_change_set(baseline, write_target, write_kind)
            failure = accounts.attempt_failure_reason(attempt, backend["host"])
            touched = changed_anything(changes)
            if "inspection_failed" in changes:
                # The run may have been perfect; nobody can say. Reporting success here would
                # hand the conductor a verdict the engine does not have.
                write_status = "unknown"
            elif failure is None:
                write_status = "succeeded" if touched else "succeeded_no_change"
            else:
                write_status = "partial" if touched else "failed"
            after = _write_manifest(write_target, write_kind)
            record = {
                "dispatch_id": dispatch_id, "at": utc_now(), "lease_generation": generation,
                "mode": "write", "status": write_status, "failure_reason": failure,
                "destination": str(write_target), "role": role,
                "backend": backend["backend"], "family": backend["family"],
                "account": backend.get("account", "private"), "model": backend.get("model"),
                "attempt": attempt_number, "changes": changes, "baseline": baseline,
                "after": (after.details or {}).get("files", {}) if after.allowed else {},
            }
            check, _ = _lease_check(bundle, task_dir, owner, generation, contract_path)
            if not check.allowed:
                print(f"warning: write record not stored: {check.reason}", file=sys.stderr)
            else:
                _state_path(task_dir, "outputs", dispatch_id, ".json").write_text(
                    json.dumps(record, indent=2), encoding="utf-8"
                )
            print(json.dumps({"write": {k: record[k] for k in
                                       ("status", "destination", "changes", "account", "model")}},
                             indent=2), file=sys.stderr)
            if touched and contract_dir is not None:
                # A failed attempt that changed files still authored those changes. Recording
                # only successes would attribute them to whoever ran next.
                authorship, _ = _lease_check(bundle, contract_dir, owner, generation, contract_path)
                if not authorship.allowed:
                    print(f"warning: authorship not recorded: {authorship.reason}", file=sys.stderr)
                else:
                    record_contributing_family(
                        contract_dir, backend["family"],
                        f"{role} via dispatch-worker --write",
                    )
            return 0 if write_status == "succeeded" else 2
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
            #
            # Guarded like every other state write, and for a sharper reason than most:
            # this file decides which family may review the artifact. Unguarded, a
            # dispatch that lost its lease could still overwrite another conductor's
            # authorship, and a symlink standing at the path could redirect the write out
            # of task state entirely.
            authorship, _ = _lease_check(bundle, contract_dir, owner, generation, contract_path)
            if not authorship.allowed:
                print(
                    f"warning: authorship not recorded: {authorship.reason}", file=sys.stderr
                )
                return status
            record_contributing_family(
                contract_dir, backend["family"], f"{role} via dispatch-worker"
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


def _read_result_file(path: Path | None) -> str | None:
    """Read a worker's result file, or ``None`` when there is nothing readable in it.

    Missing and undecodable both give ``None`` on purpose. The success predicate must not
    be able to confuse "the worker left nothing the engine can read" with "the worker
    returned an empty document": the first is ``None``, the second is ``""``, and only the
    second is a run that actually finished and said nothing.
    """
    if path is None:
        return None
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _run_worker(
    command: list[str],
    spec: WorkerCommand,
    task: dict[str, Any],
    role: str,
    brief_path: Path,
    target_repo: Path,
    backend: dict[str, Any] | None = None,
    write_destination: str | None = None,
) -> accounts.Attempt:
    """Launch a resolved worker command and return the attempt.

    ``spec.env`` is merged over a copy of the parent environment; the parent is
    never mutated. A ``claude`` run is captured so its JSON envelope can be
    classified, and only its result text is re-emitted on stdout.

    Either way the attempt carries a single ``result_text``: the envelope's ``result`` for
    Claude, the contents of ``spec.result_file`` for a host that writes one. That file is
    removed before returning, so nothing accumulates in the temporary directory and a
    later dispatch cannot read a previous one's leftovers.
    """
    brief = brief_path.read_text(encoding="utf-8")
    prompt = (
        f"Role: {role}\nTask: {task['task_id']}\n"
        "You are a bounded worker, not a conductor. Do not spawn agents or change scope. "
        + (
            f"You may write only inside {write_destination}; every other path is refused, so "
            "nothing you write elsewhere will land. "
            if write_destination is not None else
            "Do not write to the filesystem; return a structured review, evidence, or an "
            "applicable patch instead. "
        )
        + f"(Enforcement here: {spec.enforcement}.)\n\n"
        + brief
    )
    if spec.prompt_via == "argv":
        command = [*command, prompt]
    account = str((backend or {}).get("account") or "private")
    config_dir = spec.env.get("CLAUDE_CONFIG_DIR", "")
    model = str((backend or {}).get("model")) if backend else None
    env = {**os.environ, **spec.env} if spec.env else None
    capture = spec.program == "claude"

    def run(working_directory: str | Path) -> accounts.Attempt:
        """Launch the worker, feeding the prompt the way its CLI expects."""
        if spec.prompt_via == "argv":
            completed = subprocess.run(
                command, cwd=working_directory, env=env, stdin=subprocess.DEVNULL,
                text=True, check=False, capture_output=capture,
            )
        else:
            completed = subprocess.run(
                command, cwd=working_directory, env=env, input=prompt,
                text=True, check=False, capture_output=capture,
            )
        if not capture:
            classification = "ok" if completed.returncode == 0 else "error"
            return accounts.Attempt(
                account, config_dir, model, completed.returncode, classification, None, "", "",
                result_text=_read_result_file(spec.result_file),
            )
        envelope = accounts.parse_envelope(completed.stdout)
        classification = accounts.classify(completed.returncode, envelope, completed.stderr)
        returned = envelope.get("result") if isinstance(envelope, dict) else None
        # Held, not written: a rate-limited attempt is followed by a fallback attempt, and
        # emitting as we go would splice the failure text into the result the caller keeps.
        return accounts.Attempt(
            account, config_dir, model, completed.returncode, classification, envelope,
            completed.stdout, completed.stderr,
            result_text=returned if isinstance(returned, str) else None,
        )

    try:
        if not spec.isolated_cwd:
            return run(target_repo)
        with tempfile.TemporaryDirectory(prefix="multiagent-worker-") as isolated:
            return run(isolated)
    finally:
        if spec.result_file is not None:
            spec.result_file.unlink(missing_ok=True)


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
    restore_parser = subparsers.add_parser("restore-write")
    restore_parser.add_argument("--task-dir", type=Path, required=True)
    restore_parser.add_argument("--dispatch-id", required=True)
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
    dispatch_parser.add_argument(
        "--write",
        help="let the worker write this one destination (a file, or an existing directory) "
             "inside target_repo and write_scope; mutually exclusive with --out",
    )
    dispatch_parser.add_argument(
        "--min-bytes", type=int, default=MIN_PUBLISH_BYTES,
        help=f"refuse to publish a --out result shorter than this many bytes "
             f"(default {MIN_PUBLISH_BYTES}; 0 disables)",
    )
    dispatch_parser.add_argument(
        "--out",
        help="publish the worker's returned text to this path inside target_repo; "
             "the worker stays read-only and the engine performs the write",
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
    if args.command == "restore-write":
        return _print_decision(restore_write(args.task_dir, args.dispatch_id))
    if args.command == "append-event":
        return _print_decision(append_event(args.task_dir, args.owner, json.loads(args.event)))
    if args.command == "dispatch-worker":
        return dispatch_worker(
            bundle, load_document(args.task), args.role, args.brief, args.host, args.dry_run,
            args.required_family, args.task_dir or args.task.parent, args.task.parent,
            args.out, args.task, args.min_bytes, args.write,
        )
    if args.command == "self-test":
        return _print_decision(self_test(args.root))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
