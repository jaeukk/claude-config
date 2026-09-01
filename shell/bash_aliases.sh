# Portable interactive Bash helpers tracked by the claude-config repository.
# Machine-specific values belong in ~/.bash_aliases.local, which is not tracked.

if [ -r "${HOME}/.bash_aliases.local" ]; then
    # shellcheck source=/dev/null
    . "${HOME}/.bash_aliases.local"
fi

_requireBackupConfig() {
    local name
    local missing=()

    for name in BACKUP_WINDOWS_DRIVE BACKUP_VOLUME_LABEL BACKUP_MOUNT_POINT; do
        [ -n "${!name:-}" ] || missing+=("$name")
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        echo "Missing backup configuration: ${missing[*]}" >&2
        echo "Configure ~/.bash_aliases.local from ~/.claude/shell/bash_aliases.local.example." >&2
        return 1
    fi
}

mountBackup() {
    _requireBackupConfig || return 1

    local win_drive="$BACKUP_WINDOWS_DRIVE"
    local drive_letter="${win_drive%:}"
    local expected_label="$BACKUP_VOLUME_LABEL"
    local mount_point="$BACKUP_MOUNT_POINT"
    local actual_label

    if [[ ! "$win_drive" =~ ^[A-Za-z]:$ ]]; then
        echo "Invalid BACKUP_WINDOWS_DRIVE: $win_drive" >&2
        return 1
    fi
    if ! command -v powershell.exe >/dev/null 2>&1; then
        echo "mountBackup requires WSL with Windows interoperability enabled." >&2
        return 1
    fi
    if mountpoint -q "$mount_point"; then
        echo "$expected_label is already mounted at $mount_point"
        return 0
    fi

    actual_label=$(powershell.exe -NoProfile -Command \
        "(Get-Volume -DriveLetter $drive_letter -ErrorAction SilentlyContinue).FileSystemLabel" \
        2>/dev/null | tr -d '\r\n')
    if [ "$actual_label" != "$expected_label" ]; then
        echo "Refusing to mount: Windows $win_drive label is '${actual_label:-unavailable}', not '$expected_label'."
        return 1
    fi

    sudo mkdir -p "$mount_point" || return 1
    sudo mount -t drvfs "$win_drive" "$mount_point" \
        -o "uid=$(id -u),gid=$(id -g),umask=022" || return 1
    echo "Mounted $expected_label at $mount_point"
}

unmountBackup() {
    _requireBackupConfig || return 1

    local mount_point="$BACKUP_MOUNT_POINT"

    if ! mountpoint -q "$mount_point"; then
        echo "$BACKUP_VOLUME_LABEL is not mounted at $mount_point"
        return 0
    fi

    case "$PWD/" in
        "$mount_point"/*) cd "$HOME" || return 1 ;;
    esac
    sync
    if sudo umount "$mount_point"; then
        echo "Unmounted $BACKUP_VOLUME_LABEL. It can now be safely ejected from Windows."
    else
        echo "Unmount failed; close users shown by: fuser -vm $mount_point"
        return 1
    fi
}

_resolveObsidianVault() {
    local override_file="${HOME}/.claude/zotero-obsidian.local"
    local candidate=""
    local windows_user=""
    local -a candidates=()

    if [ -n "${OBSIDIAN_VAULT:-}" ]; then
        candidate="$OBSIDIAN_VAULT"
    elif [ -r "$override_file" ]; then
        IFS= read -r candidate < "$override_file"
    fi
    if [ -n "$candidate" ] && [ -d "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    while IFS= read -r candidate; do
        [ -n "$candidate" ] && candidates+=("$candidate")
    done < <(
        {
            compgen -G '/mnt/[a-z]/Users/*/My Drive/_WORKSPACE/20_Notes'
            compgen -G '/mnt/[a-z]/My Drive/_WORKSPACE/20_Notes'
        } | sort -u
    )
    if [ "${#candidates[@]}" -eq 1 ]; then
        printf '%s\n' "${candidates[0]}"
        return 0
    elif [ "${#candidates[@]}" -gt 1 ]; then
        echo "Multiple Obsidian vaults found; set OBSIDIAN_VAULT explicitly." >&2
        return 1
    fi

    windows_user=$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r\n')
    candidate="/mnt/c/Users/${windows_user}/My Drive/_WORKSPACE/20_Notes"
    if [ -n "$windows_user" ] && [ -d "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    echo "Obsidian vault not found; set OBSIDIAN_VAULT or update $override_file." >&2
    return 1
}

backupVault() {
    _requireBackupConfig || return 1

    local mount_point="$BACKUP_MOUNT_POINT"
    local retention_days="${BACKUP_RETENTION_DAYS:-30}"
    local vault_source=""
    local current_copy="${mount_point}/20_Notes"
    local history_root="${mount_point}/.backup/20_Notes"
    local history_dir="${history_root}/$(date +%Y-%m-%d_%H%M%S)"
    local verify_log=""
    local mounted_here=0
    local verified=0
    local attempt
    local -a copy_opts=(
        -a --no-owner --no-group --no-perms --modify-window=1
        --delete-delay --backup --backup-dir="$history_dir"
        --human-readable --info=progress2
    )

    if [[ ! "$retention_days" =~ ^[0-9]+$ ]]; then
        echo "BACKUP_RETENTION_DAYS must be a non-negative integer." >&2
        return 1
    fi
    vault_source=$(_resolveObsidianVault) || return 1

    if ! mountpoint -q "$mount_point"; then
        mountBackup || return 1
        mounted_here=1
    elif [ "$(findmnt -n -o SOURCE --target "$mount_point")" != "$BACKUP_WINDOWS_DRIVE" ]; then
        echo "Refusing backup: $mount_point is not mounted from Windows $BACKUP_WINDOWS_DRIVE."
        return 1
    fi

    if ! powershell.exe -NoProfile -Command \
        "if (Get-Process GoogleDriveFS -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" \
        >/dev/null 2>&1; then
        echo "Note: Google Drive for desktop is not running; backing up the local mirror as-is."
    fi

    mkdir -p "$current_copy" "$history_dir" || return 1
    verify_log=$(mktemp /tmp/backupVault.verify.XXXXXX) || return 1

    for attempt in 1 2; do
        echo "-- Vault backup pass $attempt: $vault_source -> $current_copy --"
        if ! rsync "${copy_opts[@]}" "$vault_source/" "$current_copy/"; then
            rm -f "$verify_log"
            echo "Backup failed; $BACKUP_VOLUME_LABEL remains mounted for inspection."
            return 1
        fi

        if ! rsync -rnc --delete --out-format='%i %n%L' \
            "$vault_source/" "$current_copy/" > "$verify_log"; then
            rm -f "$verify_log"
            echo "Backup verification failed; $BACKUP_VOLUME_LABEL remains mounted for inspection."
            return 1
        fi
        if [ ! -s "$verify_log" ]; then
            verified=1
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            echo "Vault changed during backup; repeating once."
        fi
    done

    if [ "$verified" -ne 1 ]; then
        echo "Vault did not stabilize. Remaining differences:"
        sed -n '1,20p' "$verify_log"
        rm -f "$verify_log"
        echo "$BACKUP_VOLUME_LABEL remains mounted; close Obsidian/Drive activity and rerun backupVault."
        return 1
    fi
    rm -f "$verify_log"

    find "$history_root" -mindepth 1 -maxdepth 1 -type d \
        -mtime "+$retention_days" -exec rm -rf -- {} +
    echo "Vault backup verified. History older than $retention_days days was removed."

    if [ "$mounted_here" -eq 1 ]; then
        unmountBackup || return 1
    else
        echo "$BACKUP_VOLUME_LABEL was already mounted, so it remains mounted."
    fi
}

_rsyncRun() {
    local -a opts=(-avul --no-owner --no-group --no-perms --info=progress2)

    if [ -f "${HOME}/.rsync-exclude" ]; then
        opts+=("--exclude-from=${HOME}/.rsync-exclude")
    fi
    rsync "${opts[@]}" "$@"
}

sync2L() {
    _requireBackupConfig || return 1
    if ! declare -p SYNC_DIRS CODES_EXCLUDES >/dev/null 2>&1; then
        echo "SYNC_DIRS and CODES_EXCLUDES must be configured in ~/.bash_aliases.local." >&2
        return 1
    fi

    local src="$BACKUP_MOUNT_POINT"
    local rsync_dst="${RSYNC_DST:-$HOME}"
    local dir

    if ! mountpoint -q "$src"; then
        echo "$BACKUP_VOLUME_LABEL is not mounted at $src; run mountBackup first."
        return 1
    fi
    _rsyncRun "${src}/.rsync-exclude" "${HOME}/.rsync-exclude" || return 1
    for dir in "${SYNC_DIRS[@]}"; do
        echo "-- Syncing $dir --"
        mkdir -p "${rsync_dst}/${dir}" || return 1
        _rsyncRun "${src}/${dir}/" "${rsync_dst}/${dir}/" || return 1
    done
    echo "-- Syncing 30_Codes (selective) --"
    mkdir -p "${rsync_dst}/30_Codes" || return 1
    _rsyncRun "${CODES_EXCLUDES[@]}" "${src}/30_Codes/" "${rsync_dst}/30_Codes/" || return 1
    echo "Done."
}

sync2E() {
    _requireBackupConfig || return 1
    if ! declare -p SYNC_DIRS CODES_EXCLUDES >/dev/null 2>&1; then
        echo "SYNC_DIRS and CODES_EXCLUDES must be configured in ~/.bash_aliases.local." >&2
        return 1
    fi

    local dst="$BACKUP_MOUNT_POINT"
    local retention_days="${BACKUP_RETENTION_DAYS:-30}"
    local backup_root="${dst}/.backup"
    local backup_dir="${backup_root}/$(date +%Y-%m-%d)"
    local dir

    if ! mountpoint -q "$dst"; then
        echo "$BACKUP_VOLUME_LABEL is not mounted at $dst; run mountBackup first."
        return 1
    fi
    _rsyncRun "${HOME}/.rsync-exclude" "${dst}/.rsync-exclude" || return 1
    for dir in "${SYNC_DIRS[@]}"; do
        echo "-- Backing up $dir to $BACKUP_VOLUME_LABEL --"
        mkdir -p "${dst}/${dir}" "${backup_dir}/${dir}" || return 1
        _rsyncRun --delete --backup --backup-dir="${backup_dir}/${dir}" \
            "${HOME}/${dir}/" "${dst}/${dir}/" || return 1
    done
    echo "-- Backing up 30_Codes (selective) to $BACKUP_VOLUME_LABEL --"
    mkdir -p "${dst}/30_Codes" "${backup_dir}/30_Codes" || return 1
    _rsyncRun "${CODES_EXCLUDES[@]}" --delete --backup \
        --backup-dir="${backup_dir}/30_Codes" "${HOME}/30_Codes/" "${dst}/30_Codes/" || return 1
    echo "-- Purging dated backups older than $retention_days days --"
    find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '????-??-??' \
        -mtime "+$retention_days" -exec rm -rf -- {} +
    echo "Done."
}

usage() {
    python3 "${HOME}/.claude/scripts/usage-watch.py" "$@"
}

canvas() {
    "${HOME}/30_Codes/claude_tools/build_concept_canvas.py" "$@"
}
