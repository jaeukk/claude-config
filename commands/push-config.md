---
description: Commit and push this machine's ~/.claude config to GitHub (origin/main)
allowed-tools: Bash(git -C *)
---

Push the local Claude config to the `claude-config` GitHub repo.

Run these steps in `~/.claude` (which is the git checkout). Use `git -C "$HOME/.claude" ...` for
every git call:

1. Show what changed: `git -C "$HOME/.claude" status --short`. If there is nothing to commit and
   nothing unpushed, report "Config already up to date" and stop.
2. Stage tracked config: `git -C "$HOME/.claude" add -A` (only portable config is tracked; secrets
   and runtime dirs are gitignored).
3. Commit with a concise message summarizing the actual changes (look at the staged diff to write
   it). Skip the commit step if there were no staged changes but there are unpushed commits.
4. Push: `git -C "$HOME/.claude" push origin main`.
5. Report the result (commit hash + that it pushed, or any error).
