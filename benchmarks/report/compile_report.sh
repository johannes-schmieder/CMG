#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REPORT_ROOT="$ROOT/benchmarks/report"
BUILD_ROOT="$ROOT/tmp/pdfs/latex"
FINAL_ROOT="$ROOT/output/pdf"
PYTHON_BIN=${CMG_REPORT_PYTHON:-python3}
MPL_CONFIG_ROOT="$BUILD_ROOT/matplotlib"
mkdir -p "$BUILD_ROOT" "$FINAL_ROOT" "$MPL_CONFIG_ROOT"

if ! pdflatex --version | head -n 1 | grep -q 'TeX Live 2023'; then
    echo 'The benchmark report must be compiled with TeX Live 2023' >&2
    exit 2
fi

if ! "$PYTHON_BIN" -c 'import matplotlib' >/dev/null 2>&1; then
    echo 'Matplotlib is required; set CMG_REPORT_PYTHON to a Python environment that provides it' >&2
    exit 2
fi
MPLCONFIGDIR="$MPL_CONFIG_ROOT" "$PYTHON_BIN" "$REPORT_ROOT/generate_current_report.py"

for required in \
    "$REPORT_ROOT/data/current_results.csv" \
    "$REPORT_ROOT/data/current_results.tex" \
    "$REPORT_ROOT/figures/current_stage_timings.pdf" \
    "$REPORT_ROOT/figures/current_stage_ratios.pdf" \
    "$REPORT_ROOT/figures/current_family_totals.pdf" \
    "$REPORT_ROOT/figures/current_family_total_ratios.pdf" \
    "$REPORT_ROOT/figures/current_memory.pdf" \
    "$REPORT_ROOT/figures/current_iterations.pdf"; do
    if [[ ! -s "$required" ]]; then
        echo "Missing report input: $required" >&2
        exit 2
    fi
done

cd "$REPORT_ROOT"
for pass in 1 2; do
    pdflatex -interaction=nonstopmode -halt-on-error -file-line-error \
        -jobname=benchmarks -output-directory="$BUILD_ROOT" benchmarks.tex \
        > "$BUILD_ROOT/pass-$pass.log"
done
if grep -En 'LaTeX Warning|Package .* Warning|Overfull \\hbox|pdfTeX warning' \
    "$BUILD_ROOT/benchmarks.log"; then
    echo 'Fatal LaTeX warning detected' >&2
    exit 3
fi
cp "$BUILD_ROOT/benchmarks.pdf" "$FINAL_ROOT/benchmarks.pdf"
pdfinfo "$FINAL_ROOT/benchmarks.pdf" > "$BUILD_ROOT/pdfinfo.txt"
echo "CMG_REPORT_SUCCESS output=$FINAL_ROOT/benchmarks.pdf"
