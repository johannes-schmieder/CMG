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
| `component-bench` | opt-in disconnected-graph sentinels, independent pruning/block-LDL switches, paired setup/solve timings, and recursive work counts |
| `component-cycle-profile` | exclusive per-level CMG phases inside certified PCG on the same component fixtures |

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

The component experiment requires an explicit feature. Its positional arguments
are measured repetitions, RHS count, and an optional case-name substring:

```bash
CMG_BENCH_COMMIT=$(git rev-parse HEAD) cargo run --release \
  --manifest-path benchmarks/Cargo.toml --features experimental-components \
  --bin component-bench -- 9 1
```

Run from a clean committed snapshot when recording evidence. Each case has two
warm-up rounds and rotates the four arms in every round. JSONL includes all raw
samples, original-system residuals/tolerances, retained/workspace bytes, and
level dimensions/visits. Graph/RHS construction is common untimed preparation.
Total time includes preconditioner setup, PCG workspace allocation, and all RHS
solves, including their normal certification and result allocation. The extra
independent residual check and isolated application measurements are outside
that total. See [the component design note](../docs/component-aware-cmg.md).

Add `--suite stress --seed 20260906` for the frozen weighted, bridge-heavy,
bipartite, heterogeneous and material-threshold cases. An optional third
positional argument filters case names by substring. Use `--route baseline`,
`prune`, `block-ldl`, `combined`, or `preserve-stopping` for a single arm. The
last arm combines block LDL with compact storage and the original vertex-based
stopping decisions. The experimental build now rotates all five arms. Without the
`experimental-components` feature this same harness builds against ordinary
CMG and runs only the baseline, permitting a separately compiled comparison.
Set `CMG_BENCH_COMMIT` at build time to record the numerical source identity.
Failures are emitted as JSONL records; the harness finishes the remaining cases
and returns a nonzero exit status. Successful samples include known-solution
relative errors in addition to residual certificates.
Complete solution vectors also receive deterministic fingerprints of their
floating-point bits outside the timed region, for cross-binary comparisons.
These fingerprints are noncryptographic. Cycle visit counts describe the
hierarchy model; execution may omit a zero-dimensional recursive correction.

For a separate requested-allocation run, enable `component-allocations` and
use one repetition. The allocator counters affect timings, so do not use those
timings for performance comparisons. Allocation records include setup peak/live
requested bytes and counts for warmed caller-buffer application and PCG loops.
These are requested layout sizes, not process RSS or allocator arena sizes.

Use `--suite large --seed 20260908` for ten fixed local cases up to 97,536
vertices: connected/mixed paths and grids, sparse/dense bipartite graphs, a
weighted mixed path and 40,000 independent pairs. The ordinary sentinels and
stress fixtures retain their original dimensions and seeds. Start the large
suite with one repetition; preserve any failed cases before increasing repeats.

Add `--profile` to attribute PCG time on the same fixtures to CMG applications,
matrix-vector products, centering, reductions, vector updates and certification.
This mode uses the existing phase profiler with one executor thread and checks
every returned solution bit and diagnostic against both scalar and planned PCG,
plus a fresh original-system residual. It emits `profile` records after two
warm-ups and `profile_structure` records for direct terminals. The latter report
nonzero factors and the full column-scan slots within grounded components.
With `experimental-components`, centering the preconditioned vector and its
residual dot product are measured together under `centering`. Compare the sum
of centering and dot-product time across source revisions with different fusion
strategies. Keep timing-harness source identical across numerical comparisons;
even an extra profiling metadata field can change the caller's compiled layout.
The fusion itself is limited to multiple components; connected inputs retain
separate subtraction and dot-product loops, and multithreaded plans retain
their original reduction trees. Solver entry selects a separately compiled PCG
loop, so the connected iteration does not carry a runtime fusion branch.
Run it separately from allocation instrumentation. Profile timers and fresh
workspaces change overhead; use ordinary paired measurements to accept speedups.

`component-cycle-profile REPETITIONS RHS_COUNT SUITE SEED [CASE_SUBSTRING]`
reports the recursive work inside the PCG preconditioner timer. For example,
run `component-cycle-profile 3 4 large 20260908` from a build with both
`experimental-components` and `cycle-profiling`. Enable `cycle-profiling` only
for separate trace builds: it implies `profiling` and adds recursive timers.
Ordinary timing builds omit it to preserve their compiled caller layout.
Its `cycle_level` records contain actual visits, stationary iterations and
exclusive initialization, smoothing, residual-matvec, restriction, centering,
prolongation and terminal times. Parent phase times exclude child recursion.
Each result is checked against the ordinary planned solver and a fresh
original-system residual. This separate executable leaves the existing timing
entry point unchanged. The public `cycle-profiling` API exposes these levels through
`PcgPhaseProfile::cycle` and profiles a standalone compatible serial application
through `CmgPreconditioner::profile_apply_compatible_into`.

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
