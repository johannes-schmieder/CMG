# CMG performance status

This is the concise recovery record for the active optimization phase. Read it together with `PERFORMANCE_PLAN.md` and the machine-readable records in `.ci/performance/` before modifying numerical source.

## Qualified repository state

- Latest fully tested source-and-test checkpoint: `decd50568508d83e78a84dd35938029ca39a206e`.
- Formatting, Clippy, rustdoc, benchmark-crate qualification, debug/release tests, release build, and Ubuntu/macOS/Windows tests: `success`.
- The dependency-free serial build and the optional `parallel` feature remain supported.
- CI status publishing rebuilds from the latest `origin/main`, preventing bookkeeping commits from displacing numerical or benchmark checkpoints.

## Retained implementation

- Deterministic stationary CMG hierarchy and certified quotient-space PCG.
- Reused immutable graph/component metadata and allocation-free repeated stationary application.
- Compressed terminal factors, direct terminal assembly, and reduced CMG/PCG workspaces.
- Checked compact `u32` retained edge endpoints with public `usize` dimensions.
- Trimmed compact graph construction, packed endpoint keys, unstable total-order sorting, and direct compact coarse contraction.
- Deterministic CSR row operators for selected parallel hierarchy levels.
- Package-owned optional Rayon executor, selectively routed `ParallelCmgPlan`, plan-aware certified PCG, and memory-bounded multi-RHS scheduling.
- Benchmark-only differential comparison against the pinned official C kernels.

## Qualified full-PCG router

The default prepared single-RHS router uses planned execution from **350,000
canonical retained edges** when a parallel hierarchy operator exists and more
than one thread is available. The threshold remains overridable through
`ParallelPcgPolicy`.

- Routing-matrix SHA: `8749fa228e0b99f555a2a5e3838c1ac8843a9cea`; run `32649078880`.
- Status: `success`.
- Numerical failures: `none`.
- Routing mismatches: `none`.
- Maximum scaled serial/planned solution difference:
  `0.000e+00`.
- Geometric planned speedup: `1.208x`.

| Case | Input edges | Canonical edges | Auto route | Planned speedup | Iterations |
|---|---:|---:|---|---:|---:|
| dense-worker-firm-1.6m | 1,600,000 | 1,599,978 | Planned | 1.777x | 9 / 9 |
| dense-worker-firm-400k | 400,000 | 399,930 | Planned | 1.361x | 9 / 9 |
| dense-worker-firm-600k | 600,000 | 599,918 | Planned | 1.446x | 9 / 9 |
| dense-worker-firm-800k | 800,000 | 799,978 | Planned | 1.491x | 9 / 9 |
| path-250k | 249,999 | 249,999 | Serial | 0.995x | 29 / 29 |
| worker-firm-300k | 300,000 | 299,996 | Serial | 1.020x | 20 / 20 |
| worker-firm-375k | 375,000 | 374,996 | Planned | 1.041x | 20 / 20 |
| worker-firm-450k | 450,000 | 449,996 | Planned | 1.038x | 20 / 20 |
| worker-firm-525k | 525,000 | 524,996 | Planned | 1.059x | 20 / 20 |
| worker-firm-600k | 600,000 | 599,996 | Planned | 1.091x | 19 / 19 |

These are directional measurements from an ordinary four-logical-CPU hosted
runner, not a claim about 8–32-core or NUMA scaling.

## Other retained evidence

- Direct compact contraction: geometric hierarchy time `0.952x`; geometric peak RSS `0.924x` across large serial/parallel cases.
- Accepted stationary cycle versus pinned C: about `0.866x` of C time on a path case and `1.008x` on a worker–firm case, with quotient-space differences around `2.1e-12` and `1.0e-15`.
- Eight-RHS throughput on the available runner: approximately `2.0x` with two threads and `2.5–2.7x` with four threads.
- The latest hierarchy phase profile identifies coarse contraction as roughly `72.4%` of attributed setup time overall, including about `65.1%` on worker–firm and `81.9%` on dense worker–firm graphs; path setup is instead dominated by forest splitting.

## Current recovery point

Single-RHS routing and threshold qualification are complete. The next performance gate is a read-only phase profile of the planned PCG outer loop. The planned path already parallelizes selected CMG applications and finest-level matvecs; dot products, norms, Krylov vector updates, component centering, and residual reconstruction remain mostly serial. No outer-PCG parallelization should be retained without an end-to-end certified solve improvement.

## Remaining major work

- Measure the full planned-PCG time share attributable to preconditioner application, matvec, reductions, vector updates, component centering, and residual certification.
- Benchmark fixed-chunk deterministic parallel dot products, norms, and vector updates only if the profile shows material headroom.
- Continue coarse-contraction/setup profiling, particularly reusable contraction buffers and routed temporary capacity.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable hardware.
- Defer panel Krylov, aggressive SIMD, and NUMA-specific tuning until ordinary deterministic paths are fully profiled.

## Certified PCG phase profile

- Minimum CMG/matvec share: `47.4%`.
- Maximum serial outer-PCG share: `49.7%`.
- Maximum vector-update share: `2.7%`.
- Evidence: `.ci/performance/pcg-phase-profile-latest.json`.

## Single-component centering gate

- Decision: `retained`.
- Validation: `success`.
- Geometric full-solve ratio: `0.827x`.
- Evidence: `.ci/performance/single-component-centering-latest.json`.

## Fused centering and norm-scale gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric full-solve ratio: `1.004x`.
- Evidence: `.ci/performance/fused-centering-norm-latest.json`.

## Parallel outer-PCG centering gate

- Decision: `not retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `1.025x`.
- Evidence: `.ci/performance/parallel-outer-centering-latest.json`.

## Fused centering and preconditioned-dot gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric full-solve ratio: `0.996x`.
- Evidence: `.ci/performance/fused-centering-dot-latest.json`.

## Exact parallel norm-scale gate

- Decision: `retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `0.971x`.
- Evidence: `.ci/performance/parallel-exact-norm-scale-latest.json`.

## Deterministic fixed-chunk dot gate

- Decision: `retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `0.960x`.
- Maximum scaled solution difference: `2.362e-10`.
- Evidence: `.ci/performance/fixed-chunk-dot-latest.json`.

## Production-reduction profiler sync

- Decision: `not retained`.
- Validation: `not_run`.
- The phase profiler now reuses the exact production planned-PCG dot and norm helpers rather than maintaining stale copies.
- Evidence: `.ci/performance/pcg-profiler-sync.json`.

