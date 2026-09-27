# Team Account Routing

How a dispatched worker ends up running against the team Claude login (`~/.claude-team`) instead of the private one. Every claim below cites the file and symbol it comes from.

## 1. What makes a backend account-bound

A registry entry in `policy/backends.yaml` is account-bound when it carries an `account` key whose value is not `private`. Two optional keys carry this: `account` (`"private"` or `"team"`) and `config_dir` (the credential directory, e.g. `~/.claude-team`).

Three team backends exist, all on `host: claude-code`, all with `config_dir: ~/.claude-team`:

- `claude-core-team` — `claude-opus-5-5`, capabilities `implementation, review, verification, structured_result`
- `claude-mid-team` — `claude-sonnet-5`, capabilities `verification, structured_result`
- `claude-fast-team` — `claude-haiku-4-5`, capabilities `bulk, mechanical, verification, structured_result`

Every other backend is either `account: private` (the five non-team Claude entries) or carries no account key at all (all `codex-*` and `agy-*`).

`validate_policy` in `engine/policy_engine.py` enforces the shape: an unknown account value is an error; `account`/`config_dir` on a non-`claude-code` host is an error; `account: "team"` without a `config_dir` is an error; and an account-bound backend must declare `write_mode: "result-only"`, on the stated ground that it "is CLI-dispatch only." All three team entries do declare `result-only`, whereas their private twins `claude-core` and `claude-mid` declare `hook-gated`.

## 2. How `resolve_binding` filters candidates

`resolve_binding` walks `binding["candidates"]` (or `["pool"]`) in declared order and returns the **first** candidate that survives every filter, so order in `bindings.yaml` is the priority:

1. **Unknown backend** — `bundle.backends.get(...)` returning `None` is skipped, not raised.
2. **Reachable hosts** — when `conductor_host` is given, `dispatch_hosts(bundle, conductor_host)` yields the adapter's `dispatch_hosts`, and a candidate whose `backend["host"]` is not in that set is skipped. An absent or malformed adapter yields the empty set, so it denies rather than admits.
3. **Required family** — `required_family is not None and family != required_family` skips.
4. **The account-bound rule** — computed as `account = backend.get("account")`, then `if account not in (None, "private"):`. Inside that branch, `exclude_account_bound` skips unconditionally, and otherwise an `availability` map marking the account unavailable (`not availability.get(account, True)`) skips. `availability=None` — validation and dry runs — treats every account as available.
5. **Family independence** — for `mode: different_family_from_author`, a candidate whose family equals `author_family` is skipped. This binding mode also fails closed before the loop: an `author_family` outside `KNOWN_FAMILIES` returns a denial rather than silently matching the first candidate.

The availability map is filled by `_resolve_with_account`, which resolves optimistically with `availability=None` first, returns early unless the winner has `account == "team"` and a `config_dir`, then calls `accounts.probe(...)`. Only if the probe says unavailable does it re-resolve with `availability={"team": False}`. Its docstring states the intended bias: "unknown quota routes to team."

## 3. Why a native subagent can never reach a team backend

`authorize_action`, handling `kind == "spawn_worker"`, ends with:

```python
return resolve_binding(
    bundle, role, author_family=author_family, conductor_host=host,
    exclude_account_bound=bool(action.get("native")),
)
```

A native spawn sets `action["native"]`, so `exclude_account_bound` is true and the branch in §2.4 (`if exclude_account_bound: continue`) drops every team candidate before any other test runs. The parameter's docstring gives the reason: "Native Task-tool children and Orca launches run under the session's own login and cannot switch." The account is selected by an environment variable on a child process (§4), and a native spawn has no such process of its own.

## 4. What `_claude_cli` does differently

`_claude_cli` builds the same argv regardless of account — `--tools Read,Grep,Glob --strict-mcp-config --effort … --model … -p --output-format json`, enforcement `restricted-tool-surface`. The one account-dependent step is the environment:

```python
env: dict[str, str] = {}
if backend.get("config_dir"):
    env["CLAUDE_CONFIG_DIR"] = str(Path(str(backend["config_dir"])).expanduser())
```

That `env` rides on `WorkerCommand.env`, described as "Environment overrides for the child (`CLAUDE_CONFIG_DIR` for an account-bound backend). Merged over a copy of the parent environment." The comment in the builder puts it plainly: "`config_dir` binds the run to one account's credentials."

A consequence lands in `build_worker_command`: under `host_mode == "wsl"`, any non-empty `spec.env` raises `NotImplementedError` — "account-bound (`CLAUDE_CONFIG_DIR`) and the WSL launcher does not forward environment; dispatch it natively from inside WSL."

## 5. Which role reaches each team backend

Per `policy/bindings.yaml`, in candidate order:

| Backend | Role | Position | Mode |
|---|---|---|---|
| `claude-core-team` | `implementer` | 1st of 3, before `claude-core`, `codex-core` | `first_available` |
| `claude-mid-team` | `verifier` | 2nd of 3, after `codex-mid`, before `claude-mid` | `different_family_from_author` |
| `claude-fast-team` | `bulk_worker` | 1st in pool, before `claude-fast`, `codex-fast` | `pool` |
| `claude-fast-team` | `runner` | 2nd of 3, after `codex-fast`, before `claude-fast` | `first_available` |

So `implementer` and `bulk_worker` prefer team first; `runner` reaches it only when `codex-fast` is unreachable; and `verifier` reaches `claude-mid-team` only when `codex-mid` is unreachable **and** `author_family != "claude"`, since the independence filter skips Claude candidates for a Claude-authored artifact.

## Caveats

- `policy/routing.yaml` was not in my input set, so the actual `conductor_adapters` / `dispatch_hosts` contents are unverified. Whether any given conductor host can in fact reach `claude-code` is therefore asserted only structurally, not from the data.
- `engine/accounts.py` was not in my input set. `accounts.probe`'s definition of "available", what `state`/`reason` it returns, and what `note_run_rate_limited` does (called at line ~1808 when a team attempt is classified `rate_limited`) are unverified.
- I did not read the dispatch path around lines 1700–1860, so how `_resolve_with_account`'s result feeds the actual subprocess launch, and any fallback-to-private behavior after a failed team attempt, is beyond what I can cite.
- `claude-core-team` and `claude-core` declare the same model (`claude-opus-5-5`) and capabilities; the only registry differences are `account`, `config_dir`, and `write_mode`. Why both exist, rather than one, is not stated in any file I read.
- The three team entries appear in two separate places in `backends.yaml` (`claude-core-team` at line 57; `claude-mid-team`/`claude-fast-team` after the `agy-*` entries at line 210). Nothing in the loader depends on order; I mention it only because it makes the registry easy to misread.