#!/usr/bin/env bash
set -euo pipefail

config_repo="${HOME}/.claude"
fragment_rel="shell/bash_aliases.sh"
fragment="${config_repo}/${fragment_rel}"
aliases_link="${HOME}/.bash_aliases"
exclude_fragment="${config_repo}/shell/rsync-exclude"
exclude_link="${HOME}/.rsync-exclude"
command_link="${HOME}/.local/bin/update-config"
local_example="${config_repo}/shell/bash_aliases.local.example"
remote_fragment=""

if [ ! -d "${config_repo}/.git" ]; then
    echo "ERROR: $config_repo is not a Git checkout." >&2
    exit 1
fi
if [ -n "$(git -C "$config_repo" status --porcelain --untracked-files=normal)" ]; then
    echo "ERROR: $config_repo has uncommitted changes; commit or stash them before updating." >&2
    git -C "$config_repo" status --short >&2
    exit 1
fi

git -C "$config_repo" fetch origin main
remote_fragment=$(mktemp /tmp/update-config.bash_aliases.XXXXXX)
trap 'rm -f "$remote_fragment"' EXIT
if git -C "$config_repo" cat-file -e "origin/main:${fragment_rel}" 2>/dev/null; then
    git -C "$config_repo" show "origin/main:${fragment_rel}" > "$remote_fragment"
    bash -n "$remote_fragment"
fi
git -C "$config_repo" merge --ff-only origin/main
bash -n "$fragment"
python3 "${config_repo}/scripts/compose-settings.py"

if [ -L "$aliases_link" ]; then
    if [ "$(readlink -f "$aliases_link")" != "$(readlink -f "$fragment")" ]; then
        echo "ERROR: $aliases_link points elsewhere; refusing to replace it." >&2
        exit 1
    fi
elif [ -e "$aliases_link" ]; then
    echo "ERROR: $aliases_link already exists and is not the managed symlink." >&2
    exit 1
else
    ln -s "$fragment" "$aliases_link"
    echo "Created $aliases_link -> $fragment"
fi

if [ -L "$exclude_link" ]; then
    if [ "$(readlink -f "$exclude_link")" != "$(readlink -f "$exclude_fragment")" ]; then
        echo "ERROR: $exclude_link points elsewhere; refusing to replace it." >&2
        exit 1
    fi
elif [ -e "$exclude_link" ]; then
    echo "ERROR: $exclude_link already exists and is not the managed symlink." >&2
    exit 1
else
    ln -s "$exclude_fragment" "$exclude_link"
    echo "Created $exclude_link -> $exclude_fragment"
fi

mkdir -p "${HOME}/.local/bin"
if [ -L "$command_link" ]; then
    if [ "$(readlink -f "$command_link")" != "$(readlink -f "$0")" ]; then
        echo "ERROR: $command_link points elsewhere; refusing to replace it." >&2
        exit 1
    fi
elif [ -e "$command_link" ]; then
    echo "ERROR: $command_link already exists and is not the managed symlink." >&2
    exit 1
else
    ln -s "$(readlink -f "$0")" "$command_link"
    echo "Created command: $command_link"
fi

if [ ! -r "${HOME}/.bash_aliases.local" ]; then
    echo "NOTICE: machine settings are missing. Copy and edit:"
    echo "  cp '$local_example' '${HOME}/.bash_aliases.local'"
fi
echo "Config updated and validated. Open a new shell or run: source ~/.bash_aliases"
