#!/usr/bin/env bash
#
# export_notconfirmed_to_pdf.sh
# -----------------------------------------------------------------------------
# Weekly scheduled task owned (documented) by the obsidian-vault-manager agent.
#
# For every ACTIVE project under 20_Notes/10_Projects/10_Active that has a
#   10_ResearchNotes/01_NotConfirmed/ folder, this script:
#     1. renders each *.md note to a PDF  (headless, via pandoc + xelatex --
#        NOT Obsidian, so dataviewjs/preamble/embed fidelity will differ),
#        written to  /mnt/c/Users/<win-user>/Downloads/INNOCORE/<project>/<note>.pdf
#     2. on SUCCESS only, moves that note's .md into the sibling 02_Confirmed/.
#   A note whose PDF fails to render is left in place and logged.
#
# It deliberately does NOT launch Obsidian (the chosen mechanism is headless).
#
# Fidelity helpers (best-effort approximation of the Obsidian render):
#   - the vault MathJax preamble (99_SYSTEM/LaTeX/LatexPreamble.md, which loads
#     the `physics` package + custom \newcommands) is translated into a real
#     LaTeX header and injected, so macros like \abs \qty \fn resolve;
#   - $$\begin{align}..\end{align}$$ is unwrapped (pandoc wraps $$ as \[ \],
#     which cannot legally contain an align env -> amsmath nesting error);
#   - ```dataviewjs / ```dataview live-dashboard blocks are dropped;
#   - ![[img|size]] embeds are rewritten to ![](<img>) + resource-path.
#
# Env toggles:
#   DRY_RUN=1     render PDFs but do NOT move any .md (safe to test).
#   ONLY=<name>   restrict to a single project folder name.
# -----------------------------------------------------------------------------
set -uo pipefail

# Keep PATH sane under cron's minimal environment.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

VAULT="/home/jaeukk/20_Notes"
ACTIVE="$VAULT/10_Projects/10_Active"
OUTBASE="/mnt/c/Users/김재욱/Downloads/INNOCORE"
PREAMBLE="$VAULT/99_SYSTEM/LaTeX/LatexPreamble.md"
LOG="/home/jaeukk/.claude/logs/export_notconfirmed.log"
ENGINE="xelatex"
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

# --- Build a LaTeX header from the vault MathJax preamble -------------------
# Translates  $\require{pkg}$ -> \usepackage{pkg}  and unwraps $...$ around
# \newcommand/\def lines. mathabx is skipped (it redefines \div and clashes).
HEADER="$(mktemp --suffix=.tex)"
{
  echo '% auto-generated for headless pandoc export; not an Obsidian artifact'
  echo '\usepackage{amsmath}\usepackage{amssymb}\usepackage{physics}\usepackage{bm}'
  if [ -f "$PREAMBLE" ]; then
    awk '
      /\$\\require\{/ { if (match($0,/\\require\{[^}]+\}/)) { s=substr($0,RSTART+9,RLENGTH-10); if (s=="mathabx") next; print "\\usepackage{" s "}" } ; next }
      /\\(newcommand|renewcommand|DeclareMathOperator|def)/ { line=$0; gsub(/^[> ]*\$/,"",line); gsub(/\$[ ]*$/,"",line); print line; next }
    ' "$PREAMBLE"
  fi
  # Safety net so stray unicode math symbols in prose are not dropped.
  echo '\usepackage{newunicodechar}'
  printf '\\newunicodechar{%s}{\\ensuremath{%s}}\n' \
    '≤' '\leq' '≥' '\geq' '≠' '\neq' '≈' '\approx' '×' '\times' '⋅' '\cdot' \
    '±' '\pm' '→' '\rightarrow' '∞' '\infty' '∂' '\partial' '∇' '\nabla' '∈' '\in' \
    'α' '\alpha' 'β' '\beta' 'γ' '\gamma' 'δ' '\delta' 'ε' '\epsilon' 'θ' '\theta' \
    'λ' '\lambda' 'μ' '\mu' 'π' '\pi' 'σ' '\sigma' 'φ' '\phi' 'χ' '\chi' 'ψ' '\psi' \
    'ω' '\omega' 'Ω' '\Omega' 'Γ' '\Gamma' 'Δ' '\Delta'
} > "$HEADER"

# Optional CJK support: only added if a Korean-capable font is installed.
CJK_ARGS=()
if fc-list 2>/dev/null | grep -qiE 'noto (sans|serif) cjk|nanum'; then
  CJK_FONT="$(fc-list 2>/dev/null | grep -iE 'noto sans cjk kr|nanumgothic' | head -1 | sed -E 's/.*: ([^:]+):.*/\1/' | sed 's/,.*//')"
  [ -n "${CJK_FONT:-}" ] && CJK_ARGS=(-V "CJKmainfont=$CJK_FONT")
fi

# Preprocess an Obsidian note into pandoc-friendly Markdown on stdout.
preprocess() {
  awk '
    NR==1 && $0=="---" { infm=1; next }
    infm==1 { if ($0=="---") infm=0; next }
    indv==1 { if ($0 ~ /^`+[[:space:]]*$/) indv=0; next }
    $0 ~ /^`+dataviewjs([[:space:]]|$)/ || $0 ~ /^`+dataview([[:space:]]|$)/ { indv=1; print "_[dataview dashboard omitted in PDF — see ResearchPlan.md]_"; next }
    { print }
  ' "$1" \
  | sed -E 's/!\[\[([^]|]+)(\|[^]]*)?\]\]/![](<\1>)/g' \
  | sed -E 's/\$\$[[:space:]]*(\\begin\{(align|aligned|gather|equation|cases|array|alignat|multline)\*?\})/\1/g; s/(\\end\{(align|aligned|gather|equation|cases|array|alignat|multline)\*?\})[[:space:]]*\$\$/\1/g'
}

cleanup() { rm -f "$HEADER"; }
trap cleanup EXIT

log "=== export run start (DRY_RUN=$DRY_RUN ONLY='${ONLY:-all}') ==="
[ -d "$ACTIVE" ] || { log "FATAL: active dir not found: $ACTIVE"; exit 1; }

total_ok=0; total_fail=0; total_moved=0

for projdir in "$ACTIVE"/*/; do
  project="$(basename "$projdir")"
  [ -n "$ONLY" ] && [ "$project" != "$ONLY" ] && continue

  src="$projdir/10_ResearchNotes/01_NotConfirmed"
  [ -d "$src" ] || continue

  dest_md="$projdir/10_ResearchNotes/02_Confirmed"
  assets="$projdir/10_ResearchNotes/_assets"
  outdir="$OUTBASE/$(outname_for "$project")"
  mkdir -p "$dest_md" "$outdir" || { log "ERROR: cannot create dirs for $project"; continue; }

  shopt -s nullglob
  for md in "$src"/*.md; do
    base="$(basename "$md" .md)"
    case "$base" in *_README) log "SKIP readme: $md"; continue;; esac

    pdf="$outdir/$base.pdf"
    tmp="$(mktemp --suffix=.md)"
    preprocess "$md" > "$tmp"

    if pandoc "$tmp" -o "$pdf" \
         --pdf-engine="$ENGINE" \
         --include-in-header="$HEADER" \
         --resource-path="$src:$assets:$projdir/10_ResearchNotes" \
         -V geometry:margin=1in -V colorlinks=true "${CJK_ARGS[@]}" \
         >>"$LOG" 2>&1 && [ -s "$pdf" ]; then
      total_ok=$((total_ok+1))
      log "OK   $project/$base.pdf"
      if [ "$DRY_RUN" = "1" ]; then
        log "DRY  would move: $md -> $dest_md/"
      else
        if mv -n "$md" "$dest_md/"; then
          total_moved=$((total_moved+1))
          log "MOVE $project/$base.md -> 02_Confirmed/"
        else
          log "WARN move failed (kept in place): $md"
        fi
      fi
    else
      total_fail=$((total_fail+1))
      log "FAIL render (kept in place): $project/$base.md"
      rm -f "$pdf"
    fi
    rm -f "$tmp"
  done
  shopt -u nullglob
done

log "=== done: ok=$total_ok fail=$total_fail moved=$total_moved ==="
exit 0
