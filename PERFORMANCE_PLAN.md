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
| P3 Frozen CSR | IN VALIDATION | CSR and edge matvec agree on exact graph families; serial crossover is measured |
| P4 Parallel solve kernels | NOT STARTED | Certified results agree across thread counts |
| P5 Multi-RHS scheduler | NOT STARTED | Throughput scales within explicit memory budgets |
| P6 Parallel setup | NOT STARTED | Hierarchy diagnostics are unchanged and setup improves |
| P7 Pinned C comparison | NOT STARTED | One-thread Rust/C kernels agree numerically and are timed equivalently |
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

### P3 design

A public `CsrLaplacian` is being added as a deterministic solve-oriented operator:

- each edge is stored in both endpoint rows;
- ordinary dimensions use four-byte neighbor indices;
- canonical edge ordering yields ascending neighbors within every row;
- each row owns one output entry, enabling later parallel matvec without atomics;
- the row kernel sums edge contributions `w * (x_i - x_j)` to stay close to the existing arithmetic;
- the canonical edge graph remains the build/provenance representation during crossover measurement.

## Approved parallel architecture

- Rayon will be optional and the serial build will remain supported.
- A package-owned custom thread pool will enforce explicit thread counts.
- Parallel reductions will use deterministic fixed chunks and fixed-order combination.
- Across-RHS and within-solve parallelism will be selected under a workspace memory budget.
- Pinned upstream C sources may be vendored only in benchmark/test infrastructure with attribution.

## Remaining hot spots

- production solve paths still use edge-scatter matvec;
- repeated RHSs are still sequential;
- coarse contraction allocates and sorts endpoint triples at each level;
- forest splitting allocates temporary traversal vectors;
- terminal setup still materializes dense temporary matrices.

## Checkpoint log

| Date | Commit | Result |
|---|---|---|
| 2026-08-22 | `cc9d641d` / `b45b252f` | Measurement infrastructure added and formatted |
| 2026-08-22 | `703ddc2e` | Graph lineage and cached components passed full CI |
| 2026-08-22 | `a6b339fd` / `8022b568` | Persistent component scratch passed all platform tests |
| 2026-08-22 | `daa677b9` / `fd8db573` | Terminal compression and CMG scratch aliasing passed all platform tests |
| 2026-08-22 | `a82155e8` | Matched A/B workflow and exact memory records added; full CI green |
| 2026-08-22 | `33392c85` | First retained performance comparison recorded |

## Current next action

Validate CSR construction and arithmetic on all platforms, add edge-versus-CSR microbenchmarks, and decide the serial routing threshold from measured results. After that, freeze a CSR operator for every hierarchy level and begin the optional custom-thread-pool implementation.