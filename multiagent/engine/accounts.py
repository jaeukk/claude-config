"""Account selection for Claude workers: team quota first, private as fallback.

Two consumers share this module: ``policy_engine.dispatch_worker`` (System B) and the
``claude-worker`` command (manual use). It probes the team account's quota, launches one
headless ``claude -p`` run under a chosen ``CLAUDE_CONFIG_DIR``, classifies the outcome,
and allows exactly one private retry after a positively classified rate-limit failure of
a read-only run. Private is never restricted; team is only ever the first try.

Runs as a script for the CLI and imports cleanly for the engine (no side effects at
import time, no network in pure functions).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRIVATE_DIR = "~/.claude"
TEAM_DIR = "~/.claude-team"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
#: Routing cutoff (used %), not literal exhaustion: leave headroom for in-flight runs.
DEFAULT_MAX_PERCENT = 95.0
CACHE_TTL_SECONDS = 60.0
#: Oldest reading that may still decide routing when the endpoint is unusable. Any
#: reading is a guess the moment another session spends the same quota; five minutes
#: bounds how long a stale high reading keeps diverting work.
STALE_TTL_SECONDS = 300.0
#: Cooldown after a probe-endpoint failure, per consecutive failure. The usage API
#: rate-limits independently of account quota and its Retry-After is useless (observed
#: value: 0), so the floor is local. Without suppression every dispatch re-probes a
#: failing endpoint.
PROBE_BACKOFF_SECONDS = (300.0, 600.0, 900.0)
#: Worker tier when the caller names no model. The shared settings.json defaults to Fable,
#: which the team plan cannot use, so a team launch must always carry an explicit model.
DEFAULT_MODEL = "opus"
#: Flags that make a run non-replayable: it continues state or can act on the world.
NO_REPLAY_FLAGS = {
    "--resume", "-r", "--continue", "-c", "--permission-mode",
    "--dangerously-skip-permissions", "--allowedTools", "--allowed-tools",
}
RATE_LIMIT_TEXT = re.compile(r"rate.?limit|usage limit|out of (?:extra )?usage|quota", re.I)


@dataclass(frozen=True)
class Availability:
    """Result of one quota probe.

    ``state`` separates the two reasons a probe says no. ``exhausted`` is evidence about
    the account; ``unknown`` is the absence of evidence, and routing must not read one as
    the other -- treating an unreadable probe as exhaustion silently drains the account
    that team-first exists to preserve, with nothing in the log saying so.
    """

    state: str  # available | exhausted | unknown
    reason: str
    windows: list[dict[str, Any]] = field(default_factory=list)
    observed_at: float = 0.0

    @property
    def available(self) -> bool:
        """Whether to route here. Unknown counts as yes: the real run decides."""
        return self.state in {"available", "unknown"}

    @property
    def known(self) -> bool:
        """Whether this reading is evidence rather than a gap."""
        return self.state != "unknown"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable form."""
        return {"state": self.state, "available": self.available, "reason": self.reason,
                "windows": self.windows}


@dataclass(frozen=True)
class Attempt:
    """One launched worker process and how it ended.

    ``result_text`` is the single value the engine consumes -- the Claude envelope's
    ``result`` or the text Codex wrote to its ``-o`` file. It is separate from ``stdout``
    because stdout also carries progress chatter and, for Codex, is not the channel the
    engine trusts. ``None`` means the run produced no readable result at all, which
    ``attempt_succeeded`` treats as failure rather than as an empty document.
    """

    account: str
    config_dir: str
    model: str | None
    exit_code: int
    classification: str  # ok | rate_limited | error | unknown
    envelope: dict[str, Any] | None
    stdout: str
    stderr: str
    result_text: str | None = None

    def as_event(self) -> dict[str, Any]:
        """Compact attribution record (no credentials, no prompt)."""
        return {
            "account": self.account,
            "config_dir": self.config_dir,
            "model": self.model,
            "exit": self.exit_code,
            "classification": self.classification,
            "api_error_status": (self.envelope or {}).get("api_error_status"),
        }


# ── probe ────────────────────────────────────────────────────────────────────


def read_access_token(config_dir: Path) -> str:
    """Return the OAuth access token stored in ``config_dir/.credentials.json``."""
    creds = json.loads((config_dir / ".credentials.json").read_text(encoding="utf-8"))
    token = (creds.get("claudeAiOauth") or creds).get("accessToken")
    if not isinstance(token, str) or not token:
        raise KeyError("accessToken missing in .credentials.json")
    return token


def fetch_usage(token: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET the usage document for one token (same call ``usage-watch.py`` makes)."""
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _scope_applies(limit: dict[str, Any], model: str | None) -> bool:
    """Whether a model-scoped window applies to the requested model.

    A scoped window names its model by display name ("Fable", "Sonnet"). It applies when
    that word appears in the requested model string; an unrecognized scope is treated as
    applicable (conservative), so an unknown full bucket sends the job to private.
    """
    scope = limit.get("scope") or {}
    display = ((scope.get("model") or {}).get("display_name") or "").strip().lower()
    if not display:
        return True
    if model is None:
        return True
    return display.split()[0] in model.lower()


def parse_limits(
    payload: dict[str, Any], model: str | None = None, max_percent: float = DEFAULT_MAX_PERCENT
) -> Availability:
    """Decide availability from a raw usage document. Pure; no I/O.

    Counted windows: ``session`` (5 h), ``weekly_all`` (7 d), and model-scoped weekly
    windows that apply to ``model``. An idle window (no ``resets_at``) counts as 0%
    when nothing is used; usage without a reset time, a malformed applicable window,
    or no applicable window at all makes the whole probe ``unknown`` → not available
    (explicit conservative fallback, never a guess).
    """
    limits = payload.get("limits")
    if not isinstance(limits, list) or not limits:
        return Availability("unknown", "probe_unknown: no limits array")
    counted: list[dict[str, Any]] = []
    for limit in limits:
        if not isinstance(limit, dict):
            return Availability("unknown", "probe_unknown: malformed limit entry")
        kind = limit.get("kind")
        if kind == "session" or kind == "weekly_all":
            applicable = True
        elif isinstance(kind, str) and kind.startswith("weekly"):
            applicable = _scope_applies(limit, model)
        else:
            continue
        if not applicable:
            continue
        percent = limit.get("percent")
        if not isinstance(percent, (int, float)) or isinstance(percent, bool) or not 0 <= percent <= 100:
            return Availability("unknown", f"probe_unknown: invalid percent for {kind}")
        resets_at = limit.get("resets_at")
        if not resets_at:
            # An idle window carries no reset time. Zero used is the most available an
            # account can be, so it counts as 0 -- skipping it made a completely unused
            # account look unreadable, which sent every job to the fallback account.
            if percent > 0:
                return Availability(
                    "unknown", f"probe_unknown: {kind} reports {percent}% used with no reset time"
                )
            counted.append({"kind": kind, "percent": 0.0, "resets_at": None})
            continue
        counted.append({"kind": kind, "percent": float(percent), "resets_at": resets_at})
    if not counted:
        return Availability("unknown", "probe_unknown: no applicable active window")
    full = [w for w in counted if w["percent"] >= max_percent]
    if full:
        worst = max(full, key=lambda w: w["percent"])
        return Availability("exhausted", f"exhausted: {worst['kind']} at {worst['percent']:.0f}%", counted)
    return Availability("available", "under cutoff", counted)


def _cache_path(cache_dir: Path, config_dir: Path) -> Path:
    creds = config_dir / ".credentials.json"
    try:
        stat = creds.stat()
        identity = f"{creds}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        identity = str(creds)
    return cache_dir / (hashlib.sha256(identity.encode()).hexdigest()[:16] + ".json")


def _earliest_reset(payload: dict[str, Any]) -> float | None:
    """Epoch of the soonest window reset in a payload, if any is parseable."""
    limits = payload.get("limits")
    soonest = None
    for limit in limits if isinstance(limits, list) else []:
        resets_at = limit.get("resets_at") if isinstance(limit, dict) else None
        if not resets_at:
            continue
        try:
            moment = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        soonest = moment if soonest is None else min(soonest, moment)
    return soonest


def _write_cache(entry: Path, data: dict[str, Any]) -> None:
    """Persist cache state; a cache failure never changes a routing decision."""
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def probe(
    config_dir: str | Path,
    model: str | None = None,
    max_percent: float | None = None,
    cache_dir: str | Path | None = None,
    now: float | None = None,
) -> Availability:
    """Probe one account's quota, cached and backed off, and classify the result.

    The cache holds the raw usage payload per credential identity, so alternating models
    reuse one reading instead of evicting each other. After a probe-endpoint failure the
    endpoint is left alone for ``PROBE_BACKOFF_SECONDS``; during that window a reading
    younger than ``STALE_TTL_SECONDS`` answers, otherwise the result is ``unknown`` --
    which still routes here. An authentication failure is never covered by a stale
    reading: it is evidence about the credential, not a transient gap. No token refresh
    is attempted.
    """
    directory = Path(config_dir).expanduser()
    cutoff = DEFAULT_MAX_PERCENT if max_percent is None else float(max_percent)
    if not 0 < cutoff <= 100:
        raise ValueError(f"max_percent must be in (0, 100], got {cutoff}")
    moment = time.time() if now is None else now
    cache_root = Path(cache_dir).expanduser() if cache_dir else Path("~/.cache/claude-worker").expanduser()
    entry = _cache_path(cache_root, directory)
    cached: dict[str, Any] | None = None
    try:
        candidate = json.loads(entry.read_text(encoding="utf-8"))
        if isinstance(candidate, dict):
            # Kept even with no payload: an entry may hold only failure bookkeeping,
            # and discarding it would reset the backoff on every dispatch.
            cached = candidate
    except (OSError, ValueError, TypeError):
        cached = None

    def from_cache(suffix: str, data: dict[str, Any]) -> Availability:
        """Evaluate a stored payload for this model and cutoff."""
        result = parse_limits(data["payload"], model, cutoff)
        return Availability(result.state, result.reason + suffix, result.windows, float(data["observed_at"]))

    def still_decisive(data: dict[str, Any], seconds: float) -> bool:
        """Whether an old reading may still route.

        Asymmetric on purpose. An ``exhausted`` reading only improves when its window
        resets, so it stays authoritative for the whole window -- known beats unknown.
        An ``available`` reading decays: another session can spend that headroom, so it
        expires at ``STALE_TTL_SECONDS`` and routing falls back to the team-first guess.
        """
        if from_cache("", data).state == "exhausted":
            return True
        return seconds < STALE_TTL_SECONDS

    # A worker's own 429 is ground truth about quota, unlike any probe reading, so it
    # decides until it lapses or a later successful probe overwrites the entry.
    if cached and float(cached.get("run_limited_until", 0)) > moment:
        return Availability(
            "exhausted",
            f"exhausted: a worker run hit 429 {int(moment - float(cached['run_limited_at']))}s ago",
        )
    has_reading = bool(cached and isinstance(cached.get("payload"), dict))
    age = moment - float(cached["observed_at"]) if has_reading else None
    # A reading is void once the window it describes has reset: its percentages
    # describe a period that no longer exists.
    expired = bool(has_reading and cached.get("earliest_reset") and moment >= float(cached["earliest_reset"]))
    usable = has_reading and not expired and age is not None
    if usable and age < CACHE_TTL_SECONDS:
        return from_cache(" (cached)", cached)
    if cached and float(cached.get("cooldown_until", 0)) > moment:
        if usable and still_decisive(cached, age):
            return from_cache(f" (stale {int(age)}s; endpoint cooling down)", cached)
        return Availability("unknown", "probe_unknown: endpoint cooling down, no usable reading")

    try:
        token = read_access_token(directory)
    except (OSError, ValueError, KeyError) as error:
        return Availability("unknown", f"probe_unknown: credentials unreadable ({error.__class__.__name__})")

    def record_failure(reason: str, auth: bool = False) -> Availability:
        """Start or lengthen the endpoint cooldown, then answer as well as we can."""
        failures = int((cached or {}).get("failures", 0)) + 1
        backoff = PROBE_BACKOFF_SECONDS[min(failures, len(PROBE_BACKOFF_SECONDS)) - 1]
        keep = dict(cached) if cached else {}
        keep.update({"failures": failures, "cooldown_until": moment + backoff})
        _write_cache(entry, keep)
        # An auth failure is evidence about the credential; a stale quota reading must
        # not paper over it. Anything else is a gap, and a recent reading may answer.
        if not auth and usable and still_decisive(cached, age):
            return from_cache(f" (stale {int(age)}s; {reason})", cached)
        return Availability("unknown", reason)

    try:
        payload = fetch_usage(token)
    except urllib.error.HTTPError as error:
        return record_failure(f"probe_unknown: HTTP {error.code}", auth=error.code in {401, 403})
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return record_failure(f"probe_unknown: {error.__class__.__name__}")

    result = parse_limits(payload, model, cutoff)
    if result.known:  # never store a payload we could not read
        _write_cache(entry, {
            "observed_at": moment, "payload": payload,
            "earliest_reset": _earliest_reset(payload), "failures": 0, "cooldown_until": 0,
        })
    return Availability(result.state, result.reason, result.windows, moment)


#: How long a worker's own rate-limit answer keeps routing away from that account. The
#: 429 carries no reset time, so this is a bounded guess that a later successful probe
#: replaces with a real reading.
RUN_LIMIT_SECONDS = 900.0


def note_run_rate_limited(
    config_dir: str | Path, cache_dir: str | Path | None = None, now: float | None = None
) -> None:
    """Record that a real run on this account was rate-limited.

    Stronger evidence than any probe: it says the account refused actual work. Recorded
    rather than merely invalidating the cache, so the next dispatch does not guess its
    way back to an account that just said no.
    """
    cache_root = Path(cache_dir).expanduser() if cache_dir else Path("~/.cache/claude-worker").expanduser()
    entry = _cache_path(cache_root, Path(config_dir).expanduser())
    moment = time.time() if now is None else now
    try:
        data = json.loads(entry.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, TypeError):
        data = {}
    data.update({"run_limited_at": moment, "run_limited_until": moment + RUN_LIMIT_SECONDS})
    _write_cache(entry, data)


# ── run + classify ───────────────────────────────────────────────────────────


def parse_envelope(stdout: str) -> dict[str, Any] | None:
    """Return the last JSON object line of a ``--output-format json`` run, if any."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("type") == "result":
                return value
    return None


def classify(exit_code: int, envelope: dict[str, Any] | None, stderr: str = "") -> str:
    """Classify one finished run. Only ``rate_limited`` ever permits a retry.

    ``api_error_status`` in the result envelope is the primary signal (HTTP 429);
    result text and stderr are secondary. Everything else that failed is ``error``;
    a nonzero exit with no envelope and no signal is ``unknown``.
    """
    # ponytail: text patterns beyond the 429 status are a heuristic; grow them from
    # captured fixtures per CLI version, never from guesses.
    if envelope is not None:
        status = envelope.get("api_error_status")
        if status == 429:
            return "rate_limited"
        if envelope.get("is_error") and RATE_LIMIT_TEXT.search(str(envelope.get("result", ""))):
            return "rate_limited"
        if envelope.get("is_error") or exit_code != 0:
            return "error"
        return "ok"
    if exit_code == 0:
        return "ok"
    if re.search(r"\b429\b|rate_limit_error", stderr):
        return "rate_limited"
    return "unknown"


def attempt_failure_reason(attempt: Attempt, host: str) -> str | None:
    """Why one finished attempt may not be published, or ``None`` if it may.

    Deliberately not ``classify``. That function answers "may this be retried", and its
    ``ok`` includes exit 0 with no envelope at all -- which for a worker whose whole
    output is a document means nothing was produced. Publication needs the opposite
    default: anything the engine cannot positively read is a failure.

    The reason is returned rather than a bare ``False`` so the provenance record can say
    *how* a run failed. ``malformed_envelope`` in particular is worth distinguishing: it
    is the one failure that looks like success from the outside, and a record that only
    said "failed" would send someone hunting through logs for a nonzero exit code that
    never happened.

    Parameters
    ----------
    attempt:
        The finished attempt, with ``result_text`` already captured by the caller.
    host:
        Backend host of the process that ran (``claude-code`` or ``codex``). The two
        hosts report success through different channels, and a host with no rule here is
        never treated as successful.

    Returns
    -------
    str or None
        ``None`` on success, otherwise one of ``malformed_envelope``, ``nonzero_exit``,
        ``missing_result_file``, ``empty_result_file``, ``unsupported_host``.
    """
    if host == "claude-code":
        envelope = attempt.envelope
        # Checked before the exit code because a missing, non-JSON, or incomplete
        # envelope is a malformed envelope at *any* exit code -- including 0, which is
        # the case that would otherwise be published as an empty document.
        if (
            not isinstance(envelope, dict)
            or envelope.get("type") != "result"
            or envelope.get("subtype") != "success"
            # Exactly false, not merely falsy: a missing key or a null must not read as
            # "no error", which is how a truncated envelope would pass as a success.
            or envelope.get("is_error", None) is not False
            or not isinstance(envelope.get("result"), str)
        ):
            return "malformed_envelope"
        return "nonzero_exit" if attempt.exit_code != 0 else None
    if host == "codex":
        if attempt.exit_code != 0:
            return "nonzero_exit"
        # The ``-o`` file: unreadable or absent leaves ``result_text`` at ``None``, and an
        # empty file is a run that finished and said nothing.
        if attempt.result_text is None:
            return "missing_result_file"
        return "empty_result_file" if attempt.result_text == "" else None
    return "unsupported_host"


def attempt_succeeded(attempt: Attempt, host: str) -> bool:
    """Whether one finished attempt produced a result the engine may publish.

    The predicate itself lives in ``attempt_failure_reason``; this is the boolean face of
    it, so the two can never disagree about what counts as success.
    """
    return attempt_failure_reason(attempt, host) is None


def run_supervised(
    argv: list[str],
    config_dir: str | Path,
    account: str,
    model: str | None,
    cwd: str | Path | None = None,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
) -> Attempt:
    """Run one worker process under ``CLAUDE_CONFIG_DIR`` and capture its envelope.

    The parent environment is copied, never mutated. stdout is captured so the JSON
    envelope can be classified; the caller decides what to emit.
    """
    directory = str(Path(config_dir).expanduser())
    child_env = {**(env if env is not None else os.environ), "CLAUDE_CONFIG_DIR": directory}
    completed = subprocess.run(
        argv, cwd=cwd, env=child_env, text=True, capture_output=True, check=False,
        input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL,
    )
    envelope = parse_envelope(completed.stdout)
    return Attempt(
        account, directory, model, completed.returncode,
        classify(completed.returncode, envelope, completed.stderr),
        envelope, completed.stdout, completed.stderr,
    )


def replay_allowed(argv: list[str]) -> bool:
    """A run may be replayed on private only if it is a plain, non-continuing ``-p`` run."""
    if not ({"-p", "--print"} & set(argv)):
        return False
    return not (NO_REPLAY_FLAGS & set(argv))


# ── CLI ──────────────────────────────────────────────────────────────────────


def _split_flag(argv: list[str], flag: str) -> tuple[list[str], str | None]:
    """Remove ``flag value`` / ``flag=value`` from argv, returning the value."""
    out: list[str] = []
    value = None
    skip = False
    for i, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if item == flag and i + 1 < len(argv):
            value, skip = argv[i + 1], True
            continue
        if item.startswith(flag + "="):
            value = item.split("=", 1)[1]
            continue
        out.append(item)
    return out, value


def _emit(event: dict[str, Any]) -> None:
    print(json.dumps({"claude-worker": event}, ensure_ascii=False), file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    """``claude-worker [--account auto|team|private] [--max-percent N] [--json] <claude args>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    args, account_opt = _split_flag(args, "--account")
    args, cutoff_opt = _split_flag(args, "--max-percent")
    want_envelope = "--json" in args
    args = [a for a in args if a != "--json"]
    args, model = _split_flag(args, "--model")
    model = model or DEFAULT_MODEL
    args, requested_format = _split_flag(args, "--output-format")
    cutoff = float(cutoff_opt) if cutoff_opt else None
    forced = account_opt if account_opt in {"team", "private"} else None

    if forced == "private":
        choice, reason = "private", "forced"
    elif forced == "team":
        choice, reason = "team", "forced"
    else:
        availability = probe(TEAM_DIR, model=model, max_percent=cutoff)
        choice = "team" if availability.available else "private"
        reason = f"{availability.state}: {availability.reason}"
    config_dirs = {"team": TEAM_DIR, "private": PRIVATE_DIR}

    headless = bool({"-p", "--print"} & set(args))
    if not headless:
        # Interactive: pick once, hand over the terminal. No replay of a conversation.
        _emit({"account": choice, "reason": reason, "mode": "interactive"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(Path(config_dirs[choice]).expanduser())}
        os.execvpe("claude", ["claude", "--model", model, *args], env)

    command = ["claude", "--model", model, *args, "--output-format", "json"]
    attempt = run_supervised(command, config_dirs[choice], choice, model)
    _emit({"attempt": 1, "reason": reason, **attempt.as_event()})
    if (
        attempt.classification == "rate_limited" and choice == "team"
        and forced is None and replay_allowed(args)
    ):
        note_run_rate_limited(TEAM_DIR)
        attempt = run_supervised(command, PRIVATE_DIR, "private", model)
        _emit({"attempt": 2, "reason": "fallback after team rate limit", **attempt.as_event()})
    if want_envelope or requested_format == "json":
        sys.stdout.write(json.dumps(attempt.envelope, ensure_ascii=False) + "\n" if attempt.envelope else attempt.stdout)
    elif attempt.envelope is not None:
        sys.stdout.write(str(attempt.envelope.get("result", "")) + "\n")
    else:
        sys.stdout.write(attempt.stdout)
    if attempt.stderr:
        sys.stderr.write(attempt.stderr)
    return attempt.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
