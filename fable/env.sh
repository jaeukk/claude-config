# fable layer 3 — model remapping (sourced from ~/.bashrc via a thin loader line)
# Wraps `claude` so that when the fable switch is ON, third-party subagents pinned
# to `model: sonnet` run on Opus 4.8 instead. Checks switch state at invocation
# time, so `fable on`/`fable off` takes effect without re-sourcing.
claude() {
    local _fable_state
    _fable_state="$(cat "$HOME/.claude/fable/state" 2>/dev/null)"
    if [ "$_fable_state" = "on" ]; then
        ANTHROPIC_DEFAULT_SONNET_MODEL="claude-opus-4-8" command claude "$@"
    else
        command claude "$@"
    fi
}
