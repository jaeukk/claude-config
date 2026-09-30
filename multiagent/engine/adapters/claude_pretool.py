"""Claude Code PreToolUse adapter for the local multi-agent policy engine."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from policy_engine import (  # noqa: E402
    _unresolved_write_reservations,
    asserted_author_families,
    observed_author_families,
    record_contributing_family,
    PRODUCING_ROLES,
    authorize_action,
    load_document,
    load_policy,
    resolve_binding,
)


MUTATING_SHELL = re.compile(
    r"(?:^|[;&|\s(`])(?:rm|mv|cp|tee|sed\s+-i|git\s+apply|patch|Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item)(?:\s|$)|(?:>>|(?<![<])>(?!>))",
    re.IGNORECASE,
)
#: Quoted strings are data, not redirects (`grep -c '^>'`, `awk '$1>9'`), unless the command hands a
#: string to a shell (`bash -c "... > f"`); fd duplication (`2>&1`), `/dev/null` and output process
#: substitution write no file. bench8's conductors hit 23 refusals, over half of them these.
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
#: `>&2`, `2>&1`, `>&-` duplicate or close a descriptor; `>& file` and `>&2.log` write a file, so the
#: target must be all digits (or `-`) up to a token boundary.
HARMLESS_REDIRECT = re.compile(r"\d*>&(?:\d+|-)(?=[\s;|&)]|$)|(?:\d*|&)>>?\|?\s*/dev/null\b|>\(")
SHELL_EVAL = re.compile(r"(?:^|[;&|\s(/])(?:bash|sh|zsh|dash|eval)(?:\s|$)")


def _literal(match: re.Match[str]) -> str:
    """A quoted string is data unless it is double-quoted and substitutes a command (``$(``, backtick)."""
    quoted = match.group(0)
    return quoted if quoted.startswith('"') and ("$(" in quoted or "`" in quoted) else "''"


def mutating_shell(command: str) -> bool:
    """Whether a shell command writes files: ``MUTATING_SHELL`` after removing what writes none."""
    text = command if SHELL_EVAL.search(command) else QUOTED.sub(_literal, command)
    return bool(MUTATING_SHELL.search(HARMLESS_REDIRECT.sub(" ", text)))


DANGEROUS_SHELL = re.compile(
    r"(?:rm\s+-rf|Remove-Item\s+.*-Recurse|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f)",
    re.IGNORECASE,
)
#: A worker CLI launched straight from the shell reaches no family-independence
#: check, because those live on the Task/MCP branch below. This catches the
#: absent-minded `codex exec ...` typed instead of `dispatch-worker`; it is a
#: HEURISTIC, not a boundary. An absolute path, a variable, or any
#: interpreter defeats it, and no regex over free-form shell can be
#: complete. Do not grow it into something that looks authoritative.
WORKER_CLI_SHELL = re.compile(r"(?:^|[;&|(]\s*)\s*(?:claude|codex|agy)(?:\.cmd)?\s", re.IGNORECASE)


#: Tool names under which Claude Code spawns a native subagent. Keep both: the hook must
#: keep working on a CLI that still says `Task`, and must not fall silent on one that says
#: `Agent`. The settings matcher in `multiagent/.claude/settings.json` lists the same names.
NATIVE_SPAWN_TOOLS = frozenset({"Task", "Agent"})


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
    # Accumulates. A native worker of one family writing over another family's retained
    # output leaves both in the artifact, and keeping only the latest would erase the earlier
    # contributor -- after which a reviewer of that family looks independent and is not.
    record_contributing_family(task_path.parent, family, source)


def observed_author(task_path: Path, task: dict[str, Any], bundle: Any) -> str | None:
    """Return the one family a reviewer must differ from, ``"mixed"`` if there are several,
    or ``None`` when independence cannot be established.

    Same rule as the engine's dispatch check, in the same order: evidence first (observed
    families), then -- only when a producer was planned and nothing was observed -- the
    contract's recorded ``authorship_assertion`` approval together with asserted families,
    then the conductor fallback for contracts that planned no producer. Assertions are
    applied *after* that resolution, as exclusions only. Reading an assertion as evidence
    here would let a conductor clear its own family's reviewer by asserting another family,
    which is the bypass the engine refuses.
    """
    contract_dir = task_path.parent
    try:
        observed = observed_author_families(contract_dir)
    except Exception:
        return None  # damaged record: deny, never guess
    asserted = asserted_author_families(contract_dir)
    planned = set(task.get("roles_plan", []) or []) & PRODUCING_ROLES
    if observed:
        contributors = list(observed)
    elif planned:
        approved = "authorship_assertion" in (task.get("approvals", {}).get("user") or [])
        if not (approved and asserted):
            return None
        contributors = list(asserted)
    else:
        # No producer was planned and nothing observed: the conductor authored it. An
        # existing sidecar with no observed section reads the same as no sidecar.
        backend = task.get("conductor", {}).get("backend")
        entry = bundle.backends.get(backend) if backend else None
        if not entry:
            return None
        contributors = [str(entry["family"])]
    excluded = sorted(set(contributors) | set(asserted))
    if len(excluded) > 1:
        # Mixed authorship -- observed, asserted, or both -- has no independent reviewer.
        # Returning one family would name a candidate that shares a family with part of the
        # artifact; a value no family can equal makes the caller deny.
        return "mixed"
    return excluded[0]


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


def evaluate(event: dict[str, Any], spawn_family: str | None = None) -> str | None:
    """Return the reason to deny one PreToolUse event, or ``None`` to allow it.

    The decision both host adapters share: this file's ``main`` for Claude Code, and
    ``codex_pretool.py``, which translates Codex events into this shape. ``spawn_family``
    names the family a native spawn will run as when the tool name does not say (a Codex
    ``spawn_agent`` child is Codex). Raises on malformed state; callers fail closed.
    """
    reasons: list[str] = []
    _evaluate(event, reasons.append, spawn_family)
    return reasons[0] if reasons else None


def main() -> int:
    """Evaluate one hook event from standard input."""
    try:
        reason = evaluate(json.load(sys.stdin))
        if reason:
            deny(reason)
        return 0
    except Exception as error:  # Claude must fail closed when a task is active or malformed.
        deny(f"multi-agent policy hook failed closed: {error}")
        return 0


def _evaluate(event: dict[str, Any], deny: Any, spawn_family: str | None = None) -> int:
    """The decision body: calls ``deny(reason)`` for a refusal. See ``evaluate``."""
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
        decision = authorize_action(bundle, task, {"kind": "write", "actor_role": actor, "path": path_value},
                                    task_dir=task_path.parent)
        if not decision.allowed:
            deny(decision.reason)
        return 0

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if WORKER_CLI_SHELL.search(command):
            deny(
                "this looks like a worker CLI launched directly, which skips the "
                "family-independence check; dispatch it with policy_engine.py "
                "dispatch-worker instead (heuristic match -- rephrase if it was quoted text)"
            )
            return 0
        if DANGEROUS_SHELL.search(command):
            decision = authorize_action(bundle, task, {"kind": "destructive_action", "actor_role": actor})
            if not decision.allowed:
                deny(decision.reason)
            return 0
        if mutating_shell(command):
            deny("shell-based mutation is forbidden during an active task (rm, mv, cp, tee, sed -i, or a "
                 "redirect into a file). Write with the Write/Edit tools instead: inside write_scope, or "
                 "ordinary files in this task's own folder. `release-lease` clears tasks/.active-task. "
                 "`2>&1`, `>/dev/null` and a quoted `>` are fine.")
        return 0

    # `Agent` is the subagent tool's name in current Claude Code; `Task` is the older
    # name. Matching only `Task` is how every native spawn bypassed this hook -- found by
    # the post-merge smoke test on 2026-09-21, not by any of eighteen review rounds.
    if tool in NATIVE_SPAWN_TOOLS or tool.startswith("mcp__codex__"):
        role = task.get("dispatch", {}).get("current_role")
        family = spawn_family or ("codex" if tool.startswith("mcp__codex__") else "claude")

        # Independence is checked against what the hook OBSERVED producing the
        # artifact, not against the self-declared field. A declaration that
        # contradicts the observation is the interesting case: it would grant
        # a same-family reviewer while the contract claims otherwise.
        if role in {"critic", "verifier"}:
            budget = task.get("audit_cycles", 0)
            if role == "critic" and (type(budget) is not int or budget < 1):
                # Same gate as dispatch-worker. Native critic rounds are not counted against
                # the budget -- nothing records them -- so only the zero budget is enforced here.
                deny(
                    f"critic blocked: audit_cycles is {budget!r}; 0, the default, means this "
                    "task skips audit. Set audit_cycles to the number of critic rounds."
                )
                return 0
            # Same refusal the engine applies on CLI dispatch: a write whose dispatcher
            # died left changed files and never recorded which family changed them, so
            # the sidecar still names the previous writer. Without this, an interrupted
            # managed write defeats independence simply by reviewing natively instead.
            pending = _unresolved_write_reservations(task_path.parent)
            if pending:
                deny(
                    f"{role} blocked: {', '.join(pending)} recorded no outcome, so what "
                    "was written and by which family are both unknown; resolve them "
                    "(restore-write --assume-stopped, or record the outcome) first"
                )
                return 0
            seen = observed_author(task_path, task, bundle)
            declared = task.get("author_family")
            if seen is None:
                deny(
                    f"{role} blocked: independence cannot be established -- a planned "
                    "producer left no observed authorship (an assertion counts only with "
                    "`authorship_assertion` under approvals.user), the authorship record "
                    "is damaged, or the conductor backend is unknown"
                )
                return 0
            if seen == "mixed":
                # Denied on its own terms. Left to the mismatch check below it would be
                # refused too -- no valid declaration equals "mixed" -- but the message
                # would blame the declaration for what is really mixed authorship.
                deny(
                    f"{role} blocked: this artifact's authorship is mixed (observed and/or "
                    "asserted); mixed authorship is refused for review and needs manual "
                    "reconciliation"
                )
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

        decision = authorize_action(
            bundle,
            task,
            {"kind": "spawn_worker", "actor_role": actor, "role": role, "native": True},
            task_dir=task_path.parent,
        )
        if not decision.allowed:
            deny(decision.reason)
            return 0
        binding = resolve_binding(
            bundle,
            str(role),
            task.get("author_family"),
            required_family=family,
            conductor_host=task.get("conductor", {}).get("host"),
            exclude_account_bound=True,  # a Task-tool child runs under the session login
        )
        if not binding.allowed:
            deny(f"current role {role} has no compatible {family} backend")
            return 0
        if role in PRODUCING_ROLES:
            # Recorded before the call runs: a PreToolUse hook cannot see the outcome. A
            # native producer needs no stated reason since 1.4.0 (single session is the
            # default), but its family is still evidence a later reviewer must differ from.
            record_observed_author(task_path, family, f"{role} via {tool}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
