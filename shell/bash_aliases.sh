# Portable interactive Bash helpers tracked by the claude-config repository.
# Machine-specific values belong in ~/.bash_aliases.local, which is not tracked.
#
# Every helper below probes for a capability rather than testing which machine
# it is on, so one code path serves WSL and native Linux alike.

if [ -r "${HOME}/.bash_aliases.local" ]; then
    # shellcheck source=/dev/null
    . "${HOME}/.bash_aliases.local"
fi

_requireBackupConfig() {
    local name
    local missing=()

    # BACKUP_WINDOWS_DRIVE is optional: it is the fallback used when the volume
    # is not visible as a block device, which is the case under WSL.
    for name in BACKUP_VOLUME_LABEL BACKUP_MOUNT_POINT; do
        [ -n "${!name:-}" ] || missing+=("$name")
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        echo "Missing backup configuration: ${missing[*]}" >&2
        echo "Configure ~/.bash_aliases.local from ~/.claude/shell/bash_aliases.local.example." >&2
        return 1
    fi
}

_backupVolumeDevice() {
    printf '%s\n' "/dev/disk/by-label/${BACKUP_VOLUME_LABEL}"
}

# Confirm the mounted filesystem really is the backup volume, by reading a marker
# file written on the volume itself. This replaces the Windows-only volume-label
# query: a file on the filesystem reads the same way everywhere, and it survives
# both relabelling and shifting drive letters.
_verifyBackupVolume() {
    local marker="${BACKUP_MOUNT_POINT}/.backup-volume"
    local seen=""

    if ! mountpoint -q "$BACKUP_MOUNT_POINT"; then
        echo "Nothing is mounted at $BACKUP_MOUNT_POINT." >&2
        return 1
    fi
    [ -r "$marker" ] && IFS= read -r seen < "$marker"
    if [ "$seen" != "$BACKUP_VOLUME_LABEL" ]; then
        echo "Refusing: $BACKUP_MOUNT_POINT is not the '$BACKUP_VOLUME_LABEL' volume." >&2
        echo "Expected $marker to contain '$BACKUP_VOLUME_LABEL'; found '${seen:-nothing}'." >&2
        echo "If this really is the backup volume, run: mountBackupInit" >&2
        return 1
    fi
}

# Write the marker once, on a volume that is already mounted and confirmed by eye.
mountBackupInit() {
    _requireBackupConfig || return 1
    if ! mountpoint -q "$BACKUP_MOUNT_POINT"; then
        echo "Mount $BACKUP_VOLUME_LABEL at $BACKUP_MOUNT_POINT first." >&2
        return 1
    fi
    printf '%s\n' "$BACKUP_VOLUME_LABEL" > "${BACKUP_MOUNT_POINT}/.backup-volume" || return 1
    echo "Marked $BACKUP_MOUNT_POINT as the '$BACKUP_VOLUME_LABEL' backup volume."
}

# Pick the mount mechanism from what this machine offers, the way
# notify-sound.sh picks an audio backend. A block device by label is the
# native-Linux route; a Windows drive letter through drvfs is the WSL route.
# The choice is made once: a failure of the chosen mechanism is a real failure,
# never a reason to try the other one and prompt for sudo.
_backupMechanism() {
    if command -v udisksctl >/dev/null 2>&1 && [ -b "$(_backupVolumeDevice)" ]; then
        echo udisks
    elif [ -n "${BACKUP_WINDOWS_DRIVE:-}" ]; then
        echo drvfs
    else
        echo "Cannot reach '$BACKUP_VOLUME_LABEL': no block device at $(_backupVolumeDevice)," >&2
        echo "and BACKUP_WINDOWS_DRIVE is unset. Is the volume plugged in?" >&2
        return 1
    fi
}

_mountBackupVolume() {
    local mechanism
    mechanism=$(_backupMechanism) || return 1

    case "$mechanism" in
        udisks)
            udisksctl mount -b "$(_backupVolumeDevice)" >/dev/null
            ;;
        drvfs)
            if [[ ! "$BACKUP_WINDOWS_DRIVE" =~ ^[A-Za-z]:$ ]]; then
                echo "Invalid BACKUP_WINDOWS_DRIVE: $BACKUP_WINDOWS_DRIVE" >&2
                return 1
            fi
            sudo mkdir -p "$BACKUP_MOUNT_POINT" || return 1
            sudo mount -t drvfs "$BACKUP_WINDOWS_DRIVE" "$BACKUP_MOUNT_POINT" \
                -o "uid=$(id -u),gid=$(id -g),umask=022"
            ;;
    esac
}

_unmountBackupVolume() {
    local mechanism
    mechanism=$(_backupMechanism) || return 1

    case "$mechanism" in
        udisks) udisksctl unmount -b "$(_backupVolumeDevice)" >/dev/null ;;
        drvfs)  sudo umount "$BACKUP_MOUNT_POINT" ;;
    esac
}

mountBackup() {
    _requireBackupConfig || return 1

    if mountpoint -q "$BACKUP_MOUNT_POINT"; then
        _verifyBackupVolume || return 1
        echo "$BACKUP_VOLUME_LABEL is already mounted at $BACKUP_MOUNT_POINT"
        return 0
    fi
    _mountBackupVolume || return 1
    if ! mountpoint -q "$BACKUP_MOUNT_POINT"; then
        echo "Mount reported success but nothing appeared at $BACKUP_MOUNT_POINT." >&2
        echo "Check where it landed: findmnt --source $(_backupVolumeDevice)" >&2
        return 1
    fi
    _verifyBackupVolume || return 1
    echo "Mounted $BACKUP_VOLUME_LABEL at $BACKUP_MOUNT_POINT"
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
    if _unmountBackupVolume; then
        echo "Unmounted $BACKUP_VOLUME_LABEL. It can now be safely unplugged."
    else
        echo "Unmount failed; close users shown by: fuser -vm $mount_point"
        return 1
    fi
}

# The Google Drive workspace mirror. Under WSL it sits on a Windows drive; on
# native Linux it is an rclone mirror under $HOME. Both are found by the same
# candidate sweep, so no machine test is needed.
_resolveWorkspaceRoot() {
    local candidate=""
    local -a candidates=()

    if [ -n "${WORKSPACE_ROOT:-}" ] && [ -d "$WORKSPACE_ROOT" ]; then
        printf '%s\n' "$WORKSPACE_ROOT"
        return 0
    fi

    while IFS= read -r candidate; do
        [ -n "$candidate" ] && candidates+=("$candidate")
    done < <(
        {
            compgen -G '/mnt/[a-z]/Users/*/My Drive/_WORKSPACE'
            compgen -G '/mnt/[a-z]/My Drive/_WORKSPACE'
            compgen -G "${HOME}/G/_WORKSPACE"
        } | sort -u
    )
    if [ "${#candidates[@]}" -eq 1 ]; then
        printf '%s\n' "${candidates[0]}"
        return 0
    elif [ "${#candidates[@]}" -gt 1 ]; then
        echo "Multiple workspaces found; set WORKSPACE_ROOT explicitly." >&2
        return 1
    fi
    echo "Workspace not found; set WORKSPACE_ROOT in ~/.bash_aliases.local." >&2
    return 1
}

_resolveObsidianVault() {
    local override_file="${HOME}/.claude/zotero-obsidian.local"
    local candidate=""
    local root=""

    if [ -n "${OBSIDIAN_VAULT:-}" ]; then
        candidate="$OBSIDIAN_VAULT"
    elif [ -r "$override_file" ]; then
        IFS= read -r candidate < "$override_file"
    fi
    if [ -n "$candidate" ] && [ -d "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    root=$(_resolveWorkspaceRoot) || return 1
    if [ -d "${root}/20_Notes" ]; then
        printf '%s\n' "${root}/20_Notes"
        return 0
    fi
    echo "No 20_Notes under $root; set OBSIDIAN_VAULT or update $override_file." >&2
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
    fi
    # Same identity check on every machine, whoever did the mounting.
    _verifyBackupVolume || return 1

    mkdir -p "$current_copy" "$history_dir" || return 1
    verify_log=$(mktemp /tmp/backupVault.verify.XXXXXX) || return 1

    # The copy-then-compare loop is what makes an external syncer safe to ignore:
    # if Google Drive or rclone rewrote the vault mid-pass, the compare is
    # non-empty and the pass repeats. That is portable, so no per-machine probe
    # of the syncing process is needed.
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
        echo "$BACKUP_VOLUME_LABEL remains mounted; stop Obsidian and the Drive/rclone sync, then rerun backupVault."
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

_requireSyncConfig() {
    if ! declare -p SYNC_DIRS CODES_EXCLUDES >/dev/null 2>&1; then
        echo "SYNC_DIRS and CODES_EXCLUDES must be configured in ~/.bash_aliases.local." >&2
        return 1
    fi
}

sync2L() {
    _requireBackupConfig || return 1
    _requireSyncConfig || return 1

    local src="$BACKUP_MOUNT_POINT"
    local rsync_dst="${RSYNC_DST:-$HOME}"
    local dir

    _verifyBackupVolume || return 1
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
    _requireSyncConfig || return 1

    local dst="$BACKUP_MOUNT_POINT"
    local src="${RSYNC_DST:-$HOME}"
    local retention_days="${BACKUP_RETENTION_DAYS:-30}"
    local backup_root="${dst}/.backup"
    local backup_dir="${backup_root}/$(date +%Y-%m-%d)"
    local dir

    _verifyBackupVolume || return 1
    # --delete makes an absent or empty source destructive: it would clear the
    # drive's copy. On a freshly imaged machine that is the normal state, so
    # refuse rather than trust the operator to notice.
    for dir in "${SYNC_DIRS[@]}" 30_Codes; do
        if [ ! -d "${src}/${dir}" ] || [ -z "$(ls -A "${src}/${dir}" 2>/dev/null)" ]; then
            echo "Refusing: ${src}/${dir} is missing or empty; --delete would clear the backup." >&2
            echo "Run sync2L first, or fix RSYNC_DST/SYNC_DIRS in ~/.bash_aliases.local." >&2
            return 1
        fi
    done

    for dir in "${SYNC_DIRS[@]}"; do
        echo "-- Backing up $dir to $BACKUP_VOLUME_LABEL --"
        mkdir -p "${dst}/${dir}" "${backup_dir}/${dir}" || return 1
        _rsyncRun --delete --backup --backup-dir="${backup_dir}/${dir}" \
            "${src}/${dir}/" "${dst}/${dir}/" || return 1
    done
    echo "-- Backing up 30_Codes (selective) to $BACKUP_VOLUME_LABEL --"
    mkdir -p "${dst}/30_Codes" "${backup_dir}/30_Codes" || return 1
    _rsyncRun "${CODES_EXCLUDES[@]}" --delete --backup \
        --backup-dir="${backup_dir}/30_Codes" "${src}/30_Codes/" "${dst}/30_Codes/" || return 1
    echo "-- Purging dated backups older than $retention_days days --"
    find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '????-??-??' \
        -mtime "+$retention_days" -exec rm -rf -- {} +
    echo "Done."
}

usage() {
    python3 "${HOME}/.claude/scripts/usage-watch.py" "$@"
}

canvas() {
    local script="${RSYNC_DST:-$HOME}/30_Codes/claude_tools/build_concept_canvas.py"

    if [ ! -f "$script" ]; then
        echo "$script not found; run sync2L to pull 30_Codes onto this machine." >&2
        return 1
    fi
    python3 "$script" "$@"
}
