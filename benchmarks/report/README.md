# Frozen first SCC study

This directory contains the source, tables, and figures for the accepted August
2026 broad size-scaling study. The final rendered report is
[`output/pdf/benchmarks.pdf`](../../output/pdf/benchmarks.pdf), and the compact
machine record is
[`scc-first-study-2026-08.json`](../../.ci/performance/scc-first-study-2026-08.json).

The report is historical evidence, not a live-results directory. Its Rust
snapshot predates the latest retained single-RHS parallel optimization. The
current matched Rust/MATLAB qualification is documented in
[`docs/PERFORMANCE.md`](../../docs/PERFORMANCE.md).

The exact first-generation harness and reduction scripts are preserved at Git
tag `benchmarks-v1-2026-08-24`. Raw repetitions, logs, resource receipts, and
SGE accounting remain in the immutable SCC run archive rather than in Git.

To rebuild the PDF from its retained inputs, use TeX Live 2023 and run:

```bash
bash benchmarks/report/compile_report.sh
```
