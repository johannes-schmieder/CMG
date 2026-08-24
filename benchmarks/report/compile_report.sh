#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
REPORT_ROOT="$ROOT/benchmarks/report"
BUILD_ROOT="$ROOT/tmp/pdfs/latex"
FINAL_ROOT="$ROOT/output/pdf"
mkdir -p "$BUILD_ROOT" "$FINAL_ROOT"

if ! pdflatex --version | head -n 1 | grep -q 'TeX Live 2023'; then
    echo 'The benchmark report must be compiled with TeX Live 2023' >&2
    exit 2
fi
for required in \
    "$REPORT_ROOT/data/results.tex" \
    "$REPORT_ROOT/figures/size_scaling_32.pdf" \
    "$REPORT_ROOT/figures/batch16_scaling.pdf" \
    "$REPORT_ROOT/figures/c_kernel_scope.pdf"; do
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
if grep -En 'LaTeX Warning|Package .* Warning|Overfull \\hbox|Underfull \\hbox|pdfTeX warning' \
    "$BUILD_ROOT/benchmarks.log"; then
    echo 'Fatal LaTeX warning detected' >&2
    exit 3
fi
cp "$BUILD_ROOT/benchmarks.pdf" "$FINAL_ROOT/benchmarks.pdf"
pdfinfo "$FINAL_ROOT/benchmarks.pdf" > "$BUILD_ROOT/pdfinfo.txt"
echo "CMG_REPORT_SUCCESS output=$FINAL_ROOT/benchmarks.pdf"
