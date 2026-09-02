# Benchmark and profiling tools

`benchmarks/` is a separate, unpublished Cargo crate. Benchmark-only code and
profiling features therefore stay out of the library package and its normal
dependency path. The command-line tools emit machine-readable JSON.

## Local tools

| Binary | Purpose |
|---|---|
| `graph-build` | canonical graph construction, including duplicate-rich inputs |
| `hierarchy-build` | serial CMG hierarchy construction |
| `hierarchy-alloc` | hierarchy construction with requested-allocation tracking |
| `terminal-build` | direct-terminal construction |
| `single-rhs-solve` | certified serial PCG with a reused hierarchy/workspace |
| `parallel-cmg-apply` | serial versus planned CMG application |
| `parallel-pcg-solve` | serial versus planned complete PCG solve |
| `full-pcg-routing` | automatic-routing crossover matrix |
| `prepared-solver-auto` | automatic versus explicit serial/within/across-RHS strategies |
| `hierarchy-phase-profile` | hierarchy phase attribution |
| `contraction-subphase-profile` | contraction subphase attribution |
| `pcg-phase-profile` | certified outer-PCG phase attribution |
| `plan-phase-profile` | parallel-plan construction attribution |
| `fixed-topology-sequence` | changing-weight assembly, caller buffers, retained preconditioners, warm starts, routing, profiles, and allocations |

The `cmg-bench` and `cmg-parallel-bench` binaries support the durable GitHub
Actions comparisons. `scc-benchmark`, `scc2-diagnostics`, and `scc2-memory`
support archived or current SCC protocols. The `scc2` names and
`cmg-scc2-v1` data identifier are retained for compatibility with immutable
run archives; the maintained SCC workflow itself now lives in `scc/`.

Build all tools with:

```bash
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

Representative runs:

```bash
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin hierarchy-build -- worker-firm 500000 5

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin single-rhs-solve -- worker-firm 100000 7

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin parallel-pcg-solve -- worker-firm 200000 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin prepared-solver-auto -- worker-firm 100000 8 7 4

cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin fixed-topology-sequence -- --case balanced --edges 25000 \
  --rhs 61 --threads 8 --repetitions 5
```

For process RSS on Linux, wrap a release binary with `/usr/bin/time -v`.

## Comparison discipline

1. Build baseline and candidate with the same compiler, features, and CPU settings.
2. Use identical deterministic graphs and right-hand sides.
3. Warm both binaries, alternate their order, and compare repeated medians.
4. Check hierarchy metadata, iterations, residual certificates, backward errors,
   and scaled solution differences before interpreting time.
5. Separate setup, application, solve, requested allocation, retained memory,
   and process RSS when the distinction matters.
6. Keep an optimization only when the end-to-end benefit justifies its memory
   and maintenance cost.

## Large-scale SCC workflow

[`scc/`](scc/) is the active, immutable-run workflow for Rust versus the
official MATLAB solver and its C MEX kernels. It creates deterministic binary
fixtures, records source and binary identities, runs SGE arrays, validates
application and scheduler results, and reduces accepted runs into tables and
figures. See [`scc/README.md`](scc/README.md) for exact commands.

The accepted current qualification is run
`20260828T021628Z-6fe9be77084a-b2v1-rust-matlab-current`. Its compact result is
[`scc-rust-matlab-current.json`](../.ci/performance/scc-rust-matlab-current.json);
full raw evidence remains in the immutable SCC archive.

The broader August 2026 size-scaling study is frozen under
[`report/`](report/) with compact record
[`scc-first-study-2026-08.json`](../.ci/performance/scc-first-study-2026-08.json).
Its original harness is available from the Git tag
`benchmarks-v1-2026-08-24`; it is not maintained alongside the current
protocol.

## Other retained evidence

`c-kernel/` is an isolated crate for bounded comparisons with pinned upstream C
kernels. It is not an end-to-end C solver. Durable machine records are indexed
by [`.ci/performance/index.json`](../.ci/performance/index.json). Current
workflow output is uploaded as GitHub Actions artifacts instead of being
committed back to `main`.
