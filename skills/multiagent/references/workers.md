# Dispatched workers: publishing, writing, inheriting

Moved verbatim from SKILL.md (2026-09-30) to keep the skill's core short. `$E` is `engine/policy_engine.py`, run from `multiagent/`.

## Publishing a worker's text: `--out`

When a worker's returned text is the deliverable (a note, a summary, a review), `--out <path>`
has the engine write it instead of the conductor:

    python3 $E dispatch-worker --task … --role bulk_worker --brief … --out notes/paper-x.md

It refuses before launch when the suffix is code, the destination is reserved
(engine state of any task, any `.git*` component, `.claude`, `.codex`, `.vscode`, `.mcp.json`),
`authorize_action` denies the path, or `--write` is also given. On success the engine writes an
immutable snapshot, then the destination, then `outputs/<dispatch_id>.json` (account, backend,
model, attempt, sha256, lease generation). An unsuccessful worker result publishes nothing; its
`status: failed` record is written only if the lease check and state write succeed. Publication is not atomic: a guard failing partway (lost lease,
narrowed scope) can leave a snapshot or the destination written with no record; inspect both
before retrying. A result under 200 UTF-8 bytes after trimming leading and trailing whitespace is `below_min_bytes` and is not
published, because a one-word refusal also exits 0; pass `--min-bytes` for genuinely short output
(`0` disables it).

## Landing files: `--write` and `--exec`

`--write <path>` gives a worker one destination (a file, or an existing directory) inside
`target_repo` and `write_scope`:

    python3 $E dispatch-worker --task … --role implementer --brief … --write src/parser.py --exec

- Only a role with `may_write` (today `implementer`), only a Claude backend, never with `--out`.
  The worker runs `--restricted` with the file tools and a generated permission file whose `Edit`
  rules name the destination; any other path has no rule and is refused. Paths containing
  `*?[]{}!` are refused, not escaped. `--write` is refused while a managed-settings file exists.
  Nothing under `~/.claude` works as a destination (the CLI asks a human).
- Before launch the engine copies the destination to `writes/<dispatch_id>.before`;
  `restore-write --task-dir … --dispatch-id <id>` puts it back, and refuses if the destination
  changed after the run was recorded. `outputs/<dispatch_id>.json` carries the change set and one
  of `succeeded`, `succeeded_no_change`, `partial` (failed but changed files), `failed`, or
  `unknown` (not inspectable; never success). A rate-limited write is not retried. Where git is
  blind, as in a vault that ignores `40_Resources/`, the baseline is the complete record.
- `--exec` (requires `--write`) adds `Bash` with a named allowlist: `python3`, `python`, `ls`,
  `cat`, `head`, `tail`, `sed -n`, `grep`, `wc`, `find`, `git diff`, `git status`, `git log`.
  **Bash writes are not confined to the destination and are outside the change set**; the brief
  is their only containment, and the recorded `enforcement` says so. Use it for code with a test
  oracle: in three runs (2026-09-30) it matched a single session's catches at the same cost (within
  run-to-run spread) and half a no-Bash implementer's tokens and time.
- A brief for summary notes must carry the note's frontmatter schema; a worker infers none of it.

## Agent workers needing other tools: the headless team driver

`paper-reviewer` and `book-summarizer` render pages, crop figures and run the vault's gates, which
are outside `--exec`'s allowlist. The sanctioned route is one headless `claude -p --agent <name>`
process per chapter under `CLAUDE_CONFIG_DIR=~/.claude-team`, cwd the vault, launched by a driver
under a contract:

    python3 _shared/adapters/book_summarizer_team.py --task-dir tasks/<id> --job tasks/<id>/job.json

Start from `_templates/book-summarizer-team/`. The conductor supplies the PDF path, page offset and
per-chapter page ranges in the job. One chapter at a time; `--jobs N` only when the user authorized
parallel chapters. On rerun, a chapter is skipped when its `x.00` overview note exceeds 2,000
bytes and carries an `agent:` line. After each attempt the driver runs `record-attempt`, and after each built chapter
`record-author`, an assertion (see `authorship.md`). The
shell grant is a named allowlist, but nothing intercepts a write: containment is the brief, and the
contract's `deviations` must say so. The model is the job's (default `claude-sonnet-5-5`).

## What a dispatched worker inherits

A Claude worker inherits nothing from your profile: `--restricted` drops user, project and local
settings, including plugins, hooks and the global `CLAUDE.md`. The engine appends a baseline (identity, American
spelling, state assumptions instead of asking, code conventions); vault layout, HPC or Zotero
details belong in the brief. The worker's cwd is `target_repo`. To read elsewhere, declare
`read_scope`: absolute existing directories, never `$HOME`, a filesystem root, or anything holding
credentials or agent configuration. The engine passes them to Claude as `--add-dir`, which also
grants write, so under `--write` every read root must equal or sit inside the destination.
