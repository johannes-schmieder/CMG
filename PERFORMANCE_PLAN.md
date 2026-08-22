# CMG Performance Optimization Plan

This document is the live recovery and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baselines and references

- Correctness baseline: `b65ae28a15f00925348046bb474c8133e5128cd0`
- Frozen benchmark harness and numerical baseline: `b45b252f88925028e3ad9a73a3f75eeab05f6754`
- Pinned official CMG source: `19752fc102f8cae8e34f66457bfaccb1aaa60375`

The matched benchmark workflow builds the frozen baseline and current candidate with the same Rust toolchain, runs deterministic inputs, uploads raw JSON, and retains the combined record at `.ci/performance/latest.json`. Hosted-runner timings are directional; large-scale and 32-core claims require a larger or self-hosted runner.

## Goals

1. Reduce setup and solve time without weakening original-system residual certification.
2. Scale useful work across machines with 1–32 or more logical CPUs.
3. Keep hierarchy and workspace memory predictable on very large graphs.
4. Preserve deterministic hierarchy construction and reproducible reductions.
5. Support both one very large solve and many RHSs sharing one hierarchy.
6. Compare optimized Rust hot kernels with pinned official C kernels where standalone comparison is possible.

## Numerical rules

- No hidden ridge, tolerance relaxation, graph mutation, or silent solver substitution.
- Every optimization must pass debug and release tests on Linux, macOS, and Windows.
- Benchmark timing is accepted only when hierarchy diagnostics, iteration counts, and certified results remain valid.
- Parallel kernels will use row/gather ownership rather than atomics or full output copies per thread.

## Phase status

| Phase | Status | Gate |
|---|---|---|
| P0 Measurement | COMPLETE | Matched baseline/candidate JSON is retained after every substantive push |
| P1 Repeated overhead | COMPLETE | Cached graph lineage/components and persistent component scratch pass full CI |
| P2 Terminal/workspace memory | COMPLETE | Factor compression and scratch aliasing pass full CI and matched numerical comparison |
| P3 Frozen CSR | COMPLETE | CSR and edge matvec agree; measured serial routing retains compact edge kernels |
| P4 Parallel solve kernels | COMPLETE | Package-owned pool and row-parallel CSR pass debug/release CI on Linux, macOS, and Windows |
| P5 Multi-RHS scheduler | COMPLETE | Ordered, memory-bounded concurrent RHS solves pass debug/release CI on Linux, macOS, and Windows |
| P6 Parallel setup | IN PROGRESS | Hierarchy diagnostics are unchanged and setup improves |
| P7 Pinned C comparison | IN PROGRESS | Pinned `sspmv` agrees numerically and is timed equivalently; restriction/prolongation and recursive-cycle comparisons remain |
| P8 Large qualification | NOT STARTED | 1–32-thread scaling and memory ceilings documented |
| P9 Advanced panel/SIMD/NUMA work | DEFERRED | Only after profiles justify complexity |

## Completed changes

### P1

- A private lineage token makes the normal cloned-graph compatibility check constant-time while preserving structural fallback for independently rebuilt equal graphs.
- PCG reuses finest-level component metadata owned by the preconditioner.
- Component projection and centering use persistent scratch in PCG and at every CMG level.
- Workspaces expose retained principal heap-byte counts.

### P2

- Terminal factorization arithmetic, pivots, ordering, and repeat-count denominator remain unchanged.
- Completed strict-lower factors select packed-triangular or sparse row/column traversal storage by retained-byte cost.
- Sparse terminal indices use `u32`; dimensions that do not fit use packed storage.
- One full vector per hierarchy level was removed by aliasing matvec and residual roles.
- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained bytes.

The first matched 20,000-vertex run preserved hierarchy levels, PCG iterations, and backward errors exactly. Relative to the frozen baseline:

| Case | Hierarchy build | CMG apply | Solve per RHS |
|---|---:|---:|---:|
| Path | 1.003x | 0.234x | 0.310x |
| Worker–firm | 0.980x | 0.992x | 0.982x |

Ratios below one favor the candidate. The path gain comes primarily from sparse terminal traversal. Worker–firm performance is approximately neutral while CMG workspace bytes decline from the baseline estimate of 1,007,440 to an exact 652,128, and PCG workspace bytes decline from 2,287,440 to 1,932,184. The path CMG workspace is 878,656 bytes versus a 1,285,160-byte baseline estimate.

### P3

A public `CsrLaplacian` is available as a deterministic solve-oriented operator:

- each edge is stored in both endpoint rows;
- ordinary dimensions use four-byte neighbor indices;
- canonical edge ordering yields ascending neighbors within every row;
- each row owns one output entry, enabling later parallel matvec without atomics;
- the row kernel sums edge contributions `w * (x_i - x_j)` to stay close to the existing arithmetic;
- the canonical edge graph remains the build/provenance and fastest serial representation.

Matched serial microbenchmarks show that duplicating undirected entries into CSR costs about 1.50x on the path case and 1.25x on the worker–firm case. Production serial solves therefore retain the compact edge-scatter kernel. CSR is reserved for parallel row ownership, where it eliminates atomics and thread-local full output vectors.

### P4/P5

- Rayon is an optional feature; default builds remain dependency-free and serial.
- `ParallelExecutor` owns an isolated custom pool with explicit or detected thread count.
- Row-parallel CSR matvec keeps each row's canonical summation order fixed.
- Concurrent repeated-RHS solves use private reusable PCG workspaces and preserve input order.
- An explicit workspace-memory budget caps simultaneous RHS solves and rejects a budget too small for one workspace.
- Serial and parallel batch outputs are checked bit-for-bit for solution vectors, iteration counts, and certified backward errors.
- The repaired all-feature tree passed formatting, Clippy with warnings denied, rustdoc with warnings denied, and debug/release tests on Ubuntu, macOS, and Windows.
- The benchmark reports actual executor threads, workspace bytes, memory-derived batch concurrency, serial/parallel batch throughput, and serial/parallel CSR matvec throughput.

### P6/P7 continuation

- Parallel graph canonicalization and coarse contraction route only above a conservative 65,536-item floor.
- Heavy-edge selection also stays on the compact serial edge scan for sparse graphs where temporary CSR construction cannot amortize.
- Forest splitting now reuses its traversal buffers instead of allocating two vectors for every leaf walk.
- Parallel-only setup helpers and thresholds are compiled only when the optional `parallel` feature is enabled, preserving warning-free dependency-free serial builds.
- The isolated benchmark crate compiles the pinned official C `sspmv` arithmetic loop without MATLAB and compares it with the Rust edge-list Laplacian matvec on identical 100,000-vertex graphs. The C code is benchmark-only and is never linked into the production library.
- The first clean hosted-runner comparison found Rust/C median time ratios of **0.849x** on the weighted path and **0.897x** on the worker–firm graph. Ratios below one favor Rust. Maximum scaled numerical differences were `4.89e-15` and `7.11e-15`, respectively.
- These C timings isolate one repeated sparse kernel. They do not yet establish full-CMG or MATLAB end-to-end superiority.

## Approved parallel architecture

- Rayon will be optional and the serial build will remain supported.
- A package-owned custom thread pool will enforce explicit thread counts.
- Parallel reductions will use deterministic fixed chunks and fixed-order combination.
- Across-RHS and within-solve parallelism will be selected under a workspace memory budget.
- Pinned upstream C sources may be vendored only in benchmark/test infrastructure with attribution.

## Remaining hot spots

- single-RHS production PCG still uses the compact serial edge-scatter operator;
- parallel within-solve CMG smoothing, restriction, prolongation, and reductions are not yet wired through every hierarchy level;
- coarse contraction allocates and sorts endpoint triples at each level;
- forest splitting remains serial, although its traversal allocations have been eliminated;
- terminal setup still materializes dense temporary matrices;
- pinned C restriction/prolongation and recursive-cycle comparisons remain to be implemented.

## Checkpoint log

| Date | Commit | Result |
|---|---|---|
| 2026-08-22 | `cc9d641d` / `b45b252f` | Measurement infrastructure added and formatted |
| 2026-08-22 | `703ddc2e` | Graph lineage and cached components passed full CI |
| 2026-08-22 | `a6b339fd` / `8022b568` | Persistent component scratch passed all platform tests |
| 2026-08-22 | `daa677b9` / `fd8db573` | Terminal compression and CMG scratch aliasing passed all platform tests |
| 2026-08-22 | `a82155e8` | Matched A/B workflow and exact memory records added; full CI green |
| 2026-08-22 | `33392c85` | First retained performance comparison recorded |
| 2026-08-22 | `31f72e6a` / `365a3572` | CSR crossover measured; full three-platform CI green |
| 2026-08-22 | `ffdc96de` / `3cff911e` | Optional package-owned Rayon pool, deterministic CSR matvec, and memory-bounded ordered batch solving added |
| 2026-08-22 | `e54024e7` | Parallel scaling benchmark added |
| 2026-08-22 | `636fa093` / `f17b377d` | Benchmark reconstruction repaired; all-feature debug/release tests passed on Ubuntu, macOS, and Windows |
| 2026-08-22 | `7e3ab0df` | Setup routing uses measured size and density floors; debug/release tests, Clippy, and rustdoc passed |
| 2026-08-22 | `fdf5b1de` | Forest traversal buffers are reused; debug/release tests, Clippy, and rustdoc passed |
| 2026-08-22 | `ba64e736` | Parallel-only helpers are gated; serial and all-feature builds are warning-free |
| 2026-08-22 | `31530bcf` / `090048bb` | Pinned C `sspmv` comparison passed and retained; Rust was faster on both hosted-runner cases |

## Current next action

Extend the standalone pinned-C harness to restriction and prolongation, then to the recursive preconditioner application. In parallel, run larger setup and repeated-RHS workloads and determine whether within-solve parallel CMG/PCG beats the existing across-RHS scheduler after accounting for memory and synchronization overhead.
