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

The default prepared single-RHS router now uses planned execution from **350,000 canonical retained edges** when a parallel hierarchy operator exists and more than one thread is available. The threshold is an overridable performance heuristic, not a mathematical CMG constant.

The latest four-thread full certified PCG matrix is recorded in `.ci/performance/full-pcg-routing-latest.json`. It reports both generated and canonical edge counts. Across ten cases:

- numerical failures: none;
- routing mismatches: none;
- maximum scaled serial/planned solution difference: `0.0`;
- serial and planned iteration counts and residual certificates: identical;
- geometric planned speedup: approximately `1.208x`.

Measured directional speedups include:

| Case | Canonical edges | Auto route | Planned speedup |
|---|---:|---|---:|
| Path | 249,999 | Serial | 0.995x |
| Sparse worker–firm | 299,996 | Serial | 1.020x |
| Sparse worker–firm | 374,996 | Planned | 1.041x |
| Sparse worker–firm | 449,996 | Planned | 1.038x |
| Sparse worker–firm | 524,996 | Planned | 1.059x |
| Sparse worker–firm | 599,996 | Planned | 1.091x |
| Dense worker–firm | 399,930 | Planned | 1.361x |
| Dense worker–firm | 599,918 | Planned | 1.446x |
| Dense worker–firm | 799,978 | Planned | 1.491x |
| Dense worker–firm | 1,599,978 | Planned | 1.777x |

These timings come from an ordinary four-logical-CPU hosted runner and are directional rather than a claim about 8–32-core or NUMA scaling.

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
