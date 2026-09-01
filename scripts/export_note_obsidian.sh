#!/usr/bin/env bash
#
# export_note_obsidian.sh  <note.md>  <out.pdf>
# -----------------------------------------------------------------------------
# Render one Obsidian note to PDF the way Obsidian's own "Export to PDF" does:
# markdown -> HTML, styled with Obsidian's real app.css (extracted from
# obsidian.asar into obsidian-assets/) plus the vault's enabled CSS snippets,
# math by MathJax with the vault preamble, then printed by headless Chrome.
#
# This replaces the earlier pandoc+xelatex path, whose LaTeX-article look does
# not resemble the app at all (no callouts, no Obsidian typography).
#
# The DOM wrapper (.print > .markdown-preview-view.markdown-rendered + an <h1>
# of the file basename) is the one the better-export-pdf plugin builds, so
# app.css applies unchanged.
# -----------------------------------------------------------------------------
set -uo pipefail

NOTE="${1:?usage: export_note_obsidian.sh <note.md> <out.pdf>}"
OUT="${2:?usage: export_note_obsidian.sh <note.md> <out.pdf>}"

HERE="$(dirname "$(readlink -f "$0")")"
ASSETS="$HERE/obsidian-assets"
FILTER="$HERE/obsidian-callouts-html.lua"
VAULT="/home/jaeukk/20_Notes"
PREAMBLE="$VAULT/99_SYSTEM/LaTeX/LatexPreamble.md"
SNIPPETS="$VAULT/.obsidian/snippets"
CHROME="${CHROME:-/mnt/c/Program Files/Google/Chrome/Application/chrome.exe}"
# Chrome is the Windows binary, so its scratch space must live on /mnt/c.
WINTMP="${WINTMP:-/mnt/c/Users/김재욱/AppData/Local/Temp/obsidian-export}"
PAPER="${PAPER:-A4}"
MARGIN="${MARGIN:-14mm}"

command -v pandoc >/dev/null || { echo "pandoc not found" >&2; exit 1; }
[ -f "$ASSETS/app.css" ] || { echo "missing $ASSETS/app.css (extract obsidian.asar)" >&2; exit 1; }
[ -x "$CHROME" ] || { echo "chrome not found at $CHROME" >&2; exit 1; }

base="$(basename "$NOTE" .md)"
notedir="$(dirname "$(readlink -f "$NOTE")")"
mkdir -p "$WINTMP" "$(dirname "$OUT")"
work="$(mktemp -d "$WINTMP/${base}.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# --- resolve an Obsidian embed target to a file:// URL Chrome can open -------
# Searches the note's own folder first, then the project's _assets, then the
# whole vault (Obsidian's own shortest-unique-path resolution).
resolve_asset() {
  local name="$1" hit=""
  for cand in "$notedir/$name" "$notedir/../_assets/$name" "$notedir/_assets/$name"; do
    [ -f "$cand" ] && { hit="$cand"; break; }
  done
  [ -n "$hit" ] || hit="$(find "$VAULT" -name "$name" -type f -print -quit 2>/dev/null)"
  [ -n "$hit" ] || return 1
  printf 'file:///%s' "$(wslpath -w "$hit" | sed 's|\\|/|g; s| |%20|g')"
}

# --- markdown -> pandoc-ready markdown --------------------------------------
# Unlike the LaTeX path this keeps $$\begin{align}, x_\max and friends exactly
# as written: MathJax accepts what Obsidian accepts, so nothing is rewritten.
preprocess() {
  awk '
    NR==1 && $0=="---" { infm=1; next }
    infm==1 { if ($0=="---") infm=0; next }
    indv==1 { if ($0 ~ /^`+[[:space:]]*$/) indv=0; next }
    $0 ~ /^`+dataviewjs([[:space:]]|$)/ || $0 ~ /^`+dataview([[:space:]]|$)/ { indv=1; print "*[dataview dashboard omitted in PDF]*"; next }
    # a standalone embed must be its own block, as it is in Obsidian
    /^!\[\[[^]]+\]\][[:space:]]*$/ { print ""; print; print ""; next }
    { print }
  ' "$1" \
  | while IFS= read -r line; do
      while [[ "$line" =~ !\[\[([^]\|]+)(\|([^]]*))?\]\] ]]; do
        target="${BASH_REMATCH[1]}"; size="${BASH_REMATCH[3]}"; whole="${BASH_REMATCH[0]}"
        if [[ "$target" =~ \.(png|jpe?g|gif|svg|webp|bmp|pdf)$ ]] && url="$(resolve_asset "$target")"; then
          if [[ "$size" =~ ^[0-9]+$ ]]; then
            repl="<img src=\"$url\" width=\"$size\">"
          else
            repl="<img src=\"$url\">"
          fi
        else
          repl="<span class=\"internal-embed\">[embedded note: $target]</span>"
        fi
        line="${line//"$whole"/$repl}"
      done
      printf '%s\n' "$line"
    done \
  | sed -E 's/\[\[([^]|]+)\|([^]]+)\]\]/<a class="internal-link" href="#">\2<\/a>/g' \
  | sed -E 's/\[\[([^]#|]+)(#[^]|]*)?\]\]/<a class="internal-link" href="#">\1<\/a>/g' \
  | sed -E 's/==([^=]+)==/<mark>\1<\/mark>/g'
}

preprocess "$NOTE" > "$work/body.md"
pandoc "$work/body.md" -f markdown-smart -t html5 --wrap=preserve \
       --mathjax --lua-filter="$FILTER" -o "$work/body.html" 2>"$work/pandoc.err" || {
  echo "pandoc failed for $NOTE" >&2; cat "$work/pandoc.err" >&2; exit 1; }

# --- MathJax preamble: the same \require/\newcommand set Obsidian loads ------
macros="$(grep -oE '\$\\(require|newcommand|renewcommand|DeclareMathOperator|def)[^$]*\$' "$PREAMBLE" 2>/dev/null | sed 's/^\$//; s/\$$//')"

css_link() { printf '<link rel="stylesheet" href="file:///%s">\n' "$(wslpath -w "$1" | sed 's|\\|/|g; s| |%20|g')"; }

{
  echo '<!doctype html><html><head><meta charset="utf-8">'
  css_link "$ASSETS/app.css"
  # the vault's own enabled snippets, so captions/math/task styling match
  if [ -d "$SNIPPETS" ]; then for s in "$SNIPPETS"/*.css; do [ -f "$s" ] && css_link "$s"; done; fi
  cat <<CSS
<style>
  @page { size: $PAPER; margin: $MARGIN; }
  body { background: #fff; }
  .print { padding: 0; }
  .markdown-preview-view { padding: 0 !important; height: auto !important; }
  img, svg, .image-embed { max-width: 100% !important; height: auto !important; }
  pre, code { white-space: pre-wrap !important; word-break: break-word; }
  table { break-inside: auto; }
  .callout, figure, img { break-inside: avoid; }
</style>
<script>
window.MathJax = {
  loader: { load: ['[tex]/physics'] },
  tex: { packages: {'[+]': ['physics']},
         inlineMath: [['\$','\$'], ['\\\\(','\\\\)']],
         displayMath: [['\$\$','\$\$'], ['\\\\[','\\\\]']] },
  startup: { typeset: true }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"></script>
</head>
<body class="theme-light mod-windows">
<div class="print"><div class="markdown-preview-view markdown-rendered show-properties">
<div style="display:none">\\(
CSS
  printf '%s\n' "$macros"
  echo '\\)</div>'
  printf '<h1 data-heading="%s">%s</h1>\n' "$base" "$base"
  cat "$work/body.html"
  echo '</div></div></body></html>'
} > "$work/note.html"

"$CHROME" --headless=new --disable-gpu --no-sandbox \
  --run-all-compositor-stages-before-draw --virtual-time-budget=30000 \
  --no-pdf-header-footer --print-to-pdf-no-header \
  --print-to-pdf="$(wslpath -w "$work/out.pdf")" \
  "$(wslpath -w "$work/note.html")" >"$work/chrome.log" 2>&1

if [ -s "$work/out.pdf" ]; then
  cp "$work/out.pdf" "$OUT"
  echo "OK $OUT"
else
  echo "chrome failed for $NOTE" >&2; tail -5 "$work/chrome.log" >&2; exit 1
fi
