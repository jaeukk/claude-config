#!/usr/bin/env bash
set -u

usage() {
  printf 'Usage: %s SOURCE_DIR [MAIN_TEX] [ENGINE]\n' "${0##*/}"
  printf 'ENGINE: none (default), pdflatex, lualatex, or xelatex\n'
}

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage >&2
  exit 64
fi

source_arg=$1
main_tex=${2:-paper.tex}
engine=${3:-none}

if [[ ! -d "$source_arg" ]]; then
  printf 'ERROR: source directory does not exist: %s\n' "$source_arg" >&2
  exit 66
fi

source_dir=$(cd "$source_arg" && pwd -P) || exit 66
if [[ ! -f "$source_dir/$main_tex" ]]; then
  printf 'ERROR: main TeX file does not exist: %s/%s\n' "$source_dir" "$main_tex" >&2
  exit 66
fi

case "$engine" in
  none|pdflatex|lualatex|xelatex) ;;
  *)
    printf 'ERROR: unsupported engine argument: %s\n' "$engine" >&2
    usage >&2
    exit 64
    ;;
esac

printf 'Source: %s\nMain:   %s\nEngine: %s\n' "$source_dir" "$main_tex" "$engine"

printf '\nGenerated files in source tree:\n'
find "$source_dir" -type f \( \
  -name '*.aux' -o -name '*.bbl' -o -name '*.blg' -o \
  -name '*.fdb_latexmk' -o -name '*.fls' -o -name '*.log' -o \
  -name '*.out' -o -name '*.pag' -o -name '*.synctex.gz' -o \
  -name '*.toc' \
\) -print | sort

printf '\nBibliography directives in main TeX file:\n'
grep -anE '\\bibliography|\\bibliographystyle|\\begin\{thebibliography\}|\\end\{thebibliography\}' \
  "$source_dir/$main_tex" || true
bibitem_count=$(grep -ac '\\bibitem' "$source_dir/$main_tex" 2>/dev/null || true)
printf 'Embedded bibitems: %s\n' "${bibitem_count:-0}"

printf '\nPotential engine-specific constructs:\n'
grep -arnE 'fontspec|setmainfont|newfontfamily|directlua|ifluatex|ifxetex|ifPDFTeX|reserveinserts' \
  "$source_dir" --include='*.tex' --include='*.cls' --include='*.sty' || true

if [[ "$engine" == none ]]; then
  exit 0
fi

for command_name in latexmk pdfinfo pdftotext; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'ERROR: required command is unavailable: %s\n' "$command_name" >&2
    exit 69
  fi
done

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/arxiv-audit.XXXXXXXX") || exit 70
cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT HUP INT TERM

case "$engine" in
  pdflatex) latexmk_mode=-pdf ;;
  lualatex) latexmk_mode=-lualatex ;;
  xelatex) latexmk_mode=-xelatex ;;
esac

printf '\nBuilding in temporary directory: %s\n' "$build_dir"
(
  cd "$source_dir" || exit 66
  TEXMFVAR="$build_dir/texmf-var" TEXMFCACHE="$build_dir/texmf-cache" \
    latexmk "$latexmk_mode" -interaction=nonstopmode -halt-on-error \
      -outdir="$build_dir" "$main_tex"
) >"$build_dir/build-output.txt" 2>&1
build_status=$?

job_name=${main_tex##*/}
job_name=${job_name%.tex}
log_file="$build_dir/$job_name.log"
pdf_file="$build_dir/$job_name.pdf"
fls_file="$build_dir/$job_name.fls"

if [[ $build_status -ne 0 ]]; then
  printf 'BUILD FAILED (status %d). Last 80 output lines:\n' "$build_status" >&2
  tail -80 "$build_dir/build-output.txt" >&2
  exit "$build_status"
fi

printf 'BUILD SUCCEEDED\n'

printf '\nSerious log diagnostics:\n'
if [[ -f "$log_file" ]]; then
  grep -anE 'Citation.*undefined|Reference.*undefined|There were undefined|LaTeX Error|Undefined control sequence|Emergency stop|Fatal error|^!' \
    "$log_file" || true
else
  printf 'WARNING: expected log file not found: %s\n' "$log_file"
fi

printf '\nPDF summary:\n'
if [[ -f "$pdf_file" ]]; then
  pdfinfo "$pdf_file" | grep -E '^(Pages|File size|PDF version):' || true
  printf '\nFirst-page text (first 20 lines):\n'
  pdftotext -f 1 -l 1 -layout "$pdf_file" - | sed -n '1,20p'
else
  printf 'WARNING: expected PDF not found: %s\n' "$pdf_file"
fi

printf '\nRecorded bibliography/font inputs of interest:\n'
if [[ -f "$fls_file" ]]; then
  grep -anE '(^|/)(fontspec[^/]*|[^/]+\.(bib|bst|bbl|ttf|otf))$|fontspec' "$fls_file" || true
else
  printf 'WARNING: expected recorder file not found: %s\n' "$fls_file"
fi
