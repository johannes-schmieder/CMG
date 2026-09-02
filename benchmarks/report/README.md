# Current technical benchmark report

This directory contains the source, retained reduction, and generated figures
for the accepted current Rust/MATLAB qualification. The rendered report is
[`output/pdf/benchmarks.pdf`](../../output/pdf/benchmarks.pdf), and the compact
accepted machine record is
[`scc-rust-matlab-current.json`](../../.ci/performance/scc-rust-matlab-current.json).

The detailed 40-row reduction in `data/current_results.csv` comes from immutable
SCC run `20260828T021628Z-6fe9be77084a-b2v1-rust-matlab-current`. The report
generator requires the complete family/implementation/CPU grid, verifies every
row's run and source identity, recomputes the geometric means, and checks them
against the compact accepted record before emitting tables or figures.

The broader first SCC study remains historical size-scaling and repeated-RHS
evidence. Its Rust snapshot predates the retained current single-RHS parallel
optimization, so its absolute latency curves are no longer used in the current
report. The compact historical record remains at
[`scc-first-study-2026-08.json`](../../.ci/performance/scc-first-study-2026-08.json),
and the exact first-generation harness is preserved at Git tag
`benchmarks-v1-2026-08-24`.

To regenerate all current figures, cross-check the accepted aggregates, and
compile the PDF with TeX Live 2023, run:

```bash
bash benchmarks/report/compile_report.sh
```

The selected Python must provide Matplotlib. Set `CMG_REPORT_PYTHON` to an
alternate interpreter when it is not available from `python3`.
