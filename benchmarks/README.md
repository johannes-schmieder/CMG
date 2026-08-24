# CMG benchmark and profiling harnesses

`benchmarks/` is a separate, non-published Cargo crate. Benchmark-only code and profiling features therefore stay outside the numerical library's normal runtime dependency path. Binaries print machine-readable JSON.

## Core benchmarks

| Binary | Purpose |
|---|---|
| `graph-build` | canonical graph construction, including duplicate-rich inputs |
| `hierarchy-build` | serial CMG hierarchy construction |
| `hierarchy-alloc` | hierarchy construction with requested-allocation tracking |
| `terminal-build` | direct-terminal construction |
| `single-rhs-solve` | certified serial PCG with a reused hierarchy/workspace |
| `parallel-cmg-apply` | serial versus selectively planned CMG application |
| `parallel-pcg-solve` | serial versus planned complete PCG solve |
| `full-pcg-routing` | routing crossover matrix used by durable workflows |
| `prepared-solver-auto` | automatic versus explicit serial/within/across-RHS strategies |

## Profiling tools

| Binary | Purpose |
|---|---|
| `hierarchy-phase-profile` | hierarchy phase attribution |
| `contraction-subphase-profile` | contraction mapping/sort/merge/finalization attribution |
| `pcg-phase-profile` | certified outer-PCG phase attribution |

`benchmarks/c-kernel/` is an isolated crate for the pinned upstream C kernel and stationary-cycle comparison.

## Build

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

Representative runs:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-build -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-alloc -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin single-rhs-solve -- worker-firm 100000 7

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin parallel-pcg-solve -- worker-firm 200000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin prepared-solver-auto -- worker-firm 100000 8 7 4
```

For process RSS on Linux, wrap the release binary with `/usr/bin/time -v`.

## Comparison protocol

1. Build baseline and candidate with the same compiler, feature set, and CPU settings.
2. Use identical deterministic graphs and RHSs.
3. Warm both binaries and interleave measured order.
4. Use medians from repeated observations.
5. Verify hierarchy metadata, iterations, residual certificates, backward errors, and scaled solution differences before interpreting time.
6. Measure setup, application, solve, requested allocation, retained memory, and process RSS separately when relevant.
7. Retain only changes with a clear end-to-end benefit and acceptable memory tradeoff.

The current tree stores only durable latest records under `.ci/performance/`. Detailed historical experiment evidence remains available through Git history and Actions artifacts. See `docs/PERFORMANCE.md`.

## SCC large-scale Rust/MATLAB study

The SCC study compares the Rust package with the official MATLAB solver and its
default C MEX build at upstream commit
`19752fc102f8cae8e34f66457bfaccb1aaa60375`. It also runs the standalone
`c-kernel` harness within its bounded hot-kernel scope. The benchmark-only Rust
entry point is `scc-benchmark`; it does not change the public library API.

The maintained workflow lives in:

| Path | Role |
|---|---|
| `src/bin/scc-benchmark.rs` | deterministic graph/vector generation and Rust stage timings |
| `matlab/scc_benchmark.m` | official CMG+MEX setup, apply, PCG, and batch timings |
| `scc/bootstrap.sh` | pinned Rust build, tests/lints, and MATLAB MEX fallback build |
| `scc/submit.sh` | smoke, main, and batch SGE submissions |
| `scc/run_array.sh` | rotated/interleaved per-task execution in `$TMPDIR` |
| `scc/validate_task.py` | per-task identity, numerical, timing, RSS, and hash checks |
| `scc/validate_run.py` | complete-result and `qacct` acceptance checks |
| `analysis/summarize.py` | compact CSV/JSON, figures, and LaTeX data generation |
| `report/benchmarks.tex` | technical report source |

On SCC, deploy an exact snapshot with `rsync` without `--delete`, record the
source and upstream checksums under the new run's `manifests/`, and run:

```bash
bash /projectnb/welfgr/cmg-benchmarks/code/benchmarks/scc/bootstrap.sh "$RUN_ID"
bash /projectnb/welfgr/cmg-benchmarks/code/benchmarks/scc/submit.sh smoke "$RUN_ID"
```

After the smoke job leaves the queue, collect and validate accounting:

```bash
bash benchmarks/scc/collect_accounting.sh "$RUN_ID" "$SMOKE_JOB_ID" 3
python3 benchmarks/scc/validate_run.py \
  "/projectnb/welfgr/cmg-benchmarks/runs/$RUN_ID" \
  benchmarks/scc/tasks-smoke.tsv "$SMOKE_JOB_ID" '1:32'
```

Only after smoke acceptance, create a new immutable production run ID, execute
`bootstrap.sh`, and submit the 15-task main array and four-task batch array:

```bash
bash benchmarks/scc/submit.sh main "$MAIN_RUN_ID"
bash benchmarks/scc/submit.sh batch "$BATCH_RUN_ID"
```

Each production task requests project `welfgr`, 32 OpenMP slots, Gold-6242 CPU
type, 8 GiB per core, whole-node linear binding, and a two-hour hard limit.
Array concurrency is capped at two; no queue is selected explicitly. Application
CPU limits are 1, 2, 4, 8, 16, and 32. Inputs and temporary solve work remain in
the scheduler-provided `$TMPDIR`.

Collect accepted run directories locally under an ignored `benchmark-runs/`
directory and reduce them with:

```bash
python3 benchmarks/analysis/repair_source_identity.py \
  benchmark-runs/RAW_MAIN_RUN_ID \
  benchmark-runs/derived/RAW_MAIN_RUN_ID
```

The repair command is not part of the normal protocol. It exists to preserve the
immutable August 2026 main archive while correcting its narrowly scoped compiled
`source_commit` bookkeeping defect. It refuses to overwrite a derived run,
requires the exact source identity from the raw manifest, permits changes only to
that JSON field, and writes a receipt with the raw-tree digest and every
before/after file hash. Use the raw run directly for future runs whose compiled
identity is already correct.

```bash
uv run --with-requirements benchmarks/analysis/requirements.txt \
  python benchmarks/analysis/summarize.py \
  --run benchmark-runs/derived/MAIN_RUN_ID \
  --run benchmark-runs/BATCH_RUN_ID \
  --figures benchmarks/report/figures \
  --summary-csv benchmarks/report/data/summary.csv \
  --results-tex benchmarks/report/data/results.tex \
  --latest-json .ci/performance/scc-latest.json

bash benchmarks/report/compile_report.sh
```

The source tree retains the compact summary, report inputs, figures, and final
PDF. Raw repetitions, logs, `/usr/bin/time -v` receipts, SGE accounting, failed
runs, and immutable manifests remain in `/projectnb/welfgr/cmg-benchmarks/runs/`.
