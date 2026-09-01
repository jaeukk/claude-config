#!/usr/bin/env bash
#
# export_notconfirmed_to_pdf.sh
# -----------------------------------------------------------------------------
# Weekly scheduled task owned (documented) by the obsidian-vault-manager agent.
#
# For every ACTIVE project under 20_Notes/10_Projects/10_Active that has a
#   10_ResearchNotes/01_NotConfirmed/ folder, this script:
#     1. renders each *.md note to a PDF at
#        /mnt/c/Users/<win-user>/Downloads/INNOCORE/<project>/<note>.pdf
#     2. on SUCCESS only, moves that note's .md into the sibling 02_Confirmed/.
#   A note whose PDF fails to render is left in place and logged.
#
# Rendering is delegated to export_note_obsidian.sh, which reproduces
# Obsidian's own "Export to PDF": HTML styled with the app's real app.css plus
# the vault's CSS snippets, MathJax with the vault preamble, printed by
# headless Chrome.  (It replaced a pandoc+xelatex path that produced a LaTeX
# article -- no callouts, wrong typography, inline-overflowing figures.)
#
# Env toggles:
#   DRY_RUN=1     render PDFs but do NOT move any .md (safe to test).
#   ONLY=<name>   restrict to a single project folder name.
# -----------------------------------------------------------------------------
set -uo pipefail

# Keep PATH sane under cron's minimal environment.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

HERE="$(dirname "$(readlink -f "$0")")"
RENDER="$HERE/export_note_obsidian.sh"
VAULT="/home/jaeukk/20_Notes"
ACTIVE="$VAULT/10_Projects/10_Active"
OUTBASE="/mnt/c/Users/김재욱/Downloads/INNOCORE"
LOG="/home/jaeukk/.claude/logs/export_notconfirmed.log"
DRY_RUN="${DRY_RUN:-0}"
ONLY="${ONLY:-}"

mkdir -p "$(dirname "$LOG")"
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG" >&2; }

# Map an active-project folder name -> its existing INNOCORE export subfolder.
# Projects not listed fall back to their own folder name.
outname_for() {
  case "$1" in
    Plasmonic)     echo "plasmon" ;;
    Multistealthy) echo "multistealthy" ;;
    *)             echo "$1" ;;
  esac
}

log "=== export run start (DRY_RUN=$DRY_RUN ONLY='${ONLY:-all}') ==="
[ -d "$ACTIVE" ] || { log "FATAL: active dir not found: $ACTIVE"; exit 1; }
[ -x "$RENDER" ] || { log "FATAL: renderer not executable: $RENDER"; exit 1; }

total_ok=0; total_fail=0; total_moved=0

for projdir in "$ACTIVE"/*/; do
  project="$(basename "$projdir")"
  [ -n "$ONLY" ] && [ "$project" != "$ONLY" ] && continue

  src="$projdir/10_ResearchNotes/01_NotConfirmed"
  [ -d "$src" ] || continue

  dest_md="$projdir/10_ResearchNotes/02_Confirmed"
  outdir="$OUTBASE/$(outname_for "$project")"
  mkdir -p "$dest_md" "$outdir" || { log "ERROR: cannot create dirs for $project"; continue; }

  shopt -s nullglob
  for md in "$src"/*.md; do
    base="$(basename "$md" .md)"
    case "$base" in *_README) log "SKIP readme: $md"; continue;; esac

    pdf="$outdir/$base.pdf"
    if "$RENDER" "$md" "$pdf" >>"$LOG" 2>&1 && [ -s "$pdf" ]; then
      total_ok=$((total_ok+1))
      log "OK   $project/$base.pdf"
      if [ "$DRY_RUN" = "1" ]; then
        log "DRY  would move: $md -> $dest_md/"
      elif mv -n "$md" "$dest_md/"; then
        total_moved=$((total_moved+1))
        log "MOVE $project/$base.md -> 02_Confirmed/"
      else
        log "WARN move failed (kept in place): $md"
      fi
    else
      total_fail=$((total_fail+1))
      log "FAIL render (kept in place): $project/$base.md"
      rm -f "$pdf"
    fi
  done
  shopt -u nullglob
done

log "=== done: ok=$total_ok fail=$total_fail moved=$total_moved ==="
exit 0
