"""Claude Code PreToolUse adapter for the local multi-agent policy engine."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from policy_engine import authorize_action, load_document, load_policy, resolve_binding  # noqa: E402


MUTATING_SHELL = re.compile(
    r"(?:^|[;&|\s])(?:rm|mv|cp|tee|sed\s+-i|git\s+apply|patch|Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item)(?:\s|$)|(?:>>|(?<![<])>(?!>))",
    re.IGNORECASE,
)
DANGEROUS_SHELL = re.compile(
    r"(?:rm\s+-rf|Remove-Item\s+.*-Recurse|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f)",
    re.IGNORECASE,
)


def deny(reason: str) -> None:
    """Emit a Claude Code hook denial."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


def active_task_path() -> Path | None:
    """Resolve the active task contract, or return ``None`` when inactive."""
    pointer = ROOT / "tasks" / ".active-task"
    if not pointer.exists():
        return None
    task_id = pointer.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", task_id):
        raise ValueError("tasks/.active-task contains an invalid task ID")
    return ROOT / "tasks" / task_id / "task.yaml"


#: Roles whose dispatch produces the artifact a critic later reviews.
PRODUCING_ROLES = frozenset({"implementer", "bulk_worker"})

#: Hook-owned sidecar. Kept out of task.yaml on purpose: the contract belongs to
#: the lease owner, while this is an observation the hook makes at dispatch time.
OBSERVED_FILE = "observed-author.json"


def record_observed_author(task_path: Path, family: str, source: str) -> None:
    """Record which family actually produced the artifact.

    Parameters
    ----------
    task_path:
        Path to the active task contract; the sidecar sits beside it.
    family:
        Family of the backend the host is really about to invoke, derived from
        the tool name rather than from anything the conductor typed.
    source:
        Human-readable provenance, e.g. ``implementer via mcp__codex__codex``.
    """
    payload = {"family": family, "source": source}
    (task_path.parent / OBSERVED_FILE).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def observed_author(task_path: Path, task: dict[str, Any], bundle: Any) -> str | None:
    """Return the observed author family, or the conductor's when none ran.

    A task where no producing worker was dispatched was authored by the
    conductor itself, so its backend family is the honest answer.
    """
    sidecar = task_path.parent / OBSERVED_FILE
    if sidecar.exists():
        try:
            return str(json.loads(sidecar.read_text(encoding="utf-8")).get("family"))
        except (json.JSONDecodeError, ValueError):
            return None
    backend = task.get("conductor", {}).get("backend")
    entry = bundle.backends.get(backend) if backend else None
    return str(entry["family"]) if entry else None


def actor_role(event: dict[str, Any], task: dict[str, Any]) -> str:
    """Infer whether the tool call belongs to the conductor or active worker."""
    if event.get("agent_id"):
        return str(task.get("dispatch", {}).get("current_role") or "unknown")
    return "conductor"


def normalized_path(tool_input: dict[str, Any]) -> str | None:
    """Extract a file path from common Claude write-tool inputs."""
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def main() -> int:
    """Evaluate one hook event from standard input."""
    try:
        event = json.load(sys.stdin)
        task_path = active_task_path()
        if task_path is None:
            return 0
        if not task_path.exists():
            deny(f"active task contract does not exist: {task_path}")
            return 0
        task = load_document(task_path)
        bundle = load_policy(ROOT)
        tool = str(event.get("tool_name", ""))
        tool_input = event.get("tool_input", {})
        if not isinstance(tool_input, dict):
            deny("tool_input must be an object")
            return 0
        actor = actor_role(event, task)

        if tool in {"Edit", "Write", "NotebookEdit"}:
            path_value = normalized_path(tool_input)
            decision = authorize_action(bundle, task, {"kind": "write", "actor_role": actor, "path": path_value})
            if not decision.allowed:
                deny(decision.reason)
            return 0

        if tool == "Bash":
            command = str(tool_input.get("command", ""))
            if DANGEROUS_SHELL.search(command):
                decision = authorize_action(bundle, task, {"kind": "destructive_action", "actor_role": actor})
                if not decision.allowed:
                    deny(decision.reason)
                return 0
            if MUTATING_SHELL.search(command):
                deny("shell-based mutation is forbidden during an active task; use hook-visible file tools")
            return 0

        if tool == "Task" or tool.startswith("mcp__codex__"):
            role = task.get("dispatch", {}).get("current_role")
            family = "codex" if tool.startswith("mcp__codex__") else "claude"

            # Independence is checked against what the hook OBSERVED producing the
            # artifact, not against the self-declared field. A declaration that
            # contradicts the observation is the interesting case: it would grant
            # a same-family reviewer while the contract claims otherwise.
            if role in {"critic", "verifier"}:
                seen = observed_author(task_path, task, bundle)
                declared = task.get("author_family")
                if seen is None:
                    deny(f"{role} blocked: no observed author family recorded for this task")
                    return 0
                if declared != seen:
                    deny(
                        f"{role} blocked: task declares author_family={declared!r} but the "
                        f"hook observed {seen!r} producing this task's artifact"
                    )
                    return 0
                if family == seen:
                    deny(f"{role} blocked: {family} cannot review an artifact {seen} produced")
                    return 0

            decision = authorize_action(bundle, task, {"kind": "spawn_worker", "actor_role": actor, "role": role})
            if not decision.allowed:
                deny(decision.reason)
                return 0
            binding = resolve_binding(bundle, str(role), task.get("author_family"), required_family=family)
            if not binding.allowed:
                deny(f"current role {role} has no compatible {family} backend")
                return 0
            if role in PRODUCING_ROLES:
                record_observed_author(task_path, family, f"{role} via {tool}")
            return 0
        return 0
    except Exception as error:  # Claude must fail closed when a task is active or malformed.
        deny(f"multi-agent policy hook failed closed: {error}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
