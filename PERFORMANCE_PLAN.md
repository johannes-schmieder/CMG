# CMG Performance Optimization Plan

This document is the live recovery and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baseline

- Correctness-qualified starting commit: `b65ae28a15f00925348046bb474c8133e5128cd0`
- Benchmark-harness baseline: `b45b252f88925028e3ad9a73a3f75eeab05f6754`
- Pinned upstream CMG reference: `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- Baseline implementation: deterministic, single-threaded, canonical edge-list Laplacians, dense grounded terminal LDL, sequential repeated-RHS solves.
- Performance claims at baseline: none beyond algorithmic equivalence and small-problem correctness.

## Goals

1. Reduce total setup and solve time without weakening residual certification.
2. Scale useful work across machines with 1–32 or more logical CPUs.
3. Keep memory predictable on graphs with very large vertex and edge counts.
4. Preserve deterministic hierarchy construction and reproducible results.
5. Support both one very large RHS solve and many RHSs sharing one hierarchy.
6. Compare optimized Rust hot kernels with the pinned official C kernels where a standalone comparison is possible.

## Non-negotiable numerical rules

- The original graph system remains the source of convergence certification.
- No hidden ridge, tolerance relaxation, graph mutation, or silent solver substitution.
- Parallel execution must preserve a fixed reduction structure where practical.
- Every optimization must pass debug and release tests on Linux, macOS, and Windows.
- Benchmark results are accepted only when the numerical result and hierarchy diagnostics remain valid.

## Measurement protocol

The benchmark driver reports input generation, graph construction, hierarchy construction, one CMG application, certified PCG solves, iteration counts, backward error, hierarchy structure, and retained storage. The workflow builds the frozen benchmark baseline and current candidate with the same toolchain and runs both on the same deterministic inputs. It persists the combined record at `.ci/performance/latest.json` and uploads the raw results.

Hosted-runner timings are directional evidence rather than stable regression thresholds. Large-scale and 32-core claims require a larger or self-hosted runner.

Graph families include paths, grids, bottleneck graphs, heavy-tailed bipartite worker–firm graphs, duplicate edges, heterogeneous weights, and deterministic synthetic random graphs. Target sizes range from 10,000-edge CI smoke tests through 10-million-edge dedicated runs. Shared-hierarchy workloads use 1, 4, 8, 32, and 128 right-hand sides. Thread counts are 1, 2, 4, 8, 16, 32, and higher where hardware permits.

## Phases

| Phase | Status | Scope | Gate |
|---|---|---|---|
| P0 Measurement | COMPLETE | Matched benchmark driver, workflow, frozen baseline, retained result, memory reports | Candidate/baseline JSON produced on identical inputs |
| P1 Remove repeated overhead | COMPLETE | Cache components and graph identity; remove recurring component allocations | Same solutions and iterations on all platforms |
| P2 Terminal and workspace memory | COMPLETE | Compress terminal traversal; alias same-level matvec/residual scratch; exact byte reports | All debug/release platform tests pass |
| P3 Frozen CSR operator | NEXT | Deterministic row representation and grouped aggregate/component membership | Serial CSR correctness and measured crossover policy |
| P4 Parallel solve kernels | NOT STARTED | Package-owned thread pool; parallel matvec, vector updates, restriction, prolongation, reductions | Equivalent certified answers across thread counts |
| P5 Multi-RHS scheduler | NOT STARTED | Memory-aware concurrency across independent solves | Throughput scaling without uncontrolled memory growth |
| P6 Parallel setup | NOT STARTED | Parallel canonicalization, contraction, sorting, and heavy-edge selection | Preserved hierarchy diagnostics and faster setup |
| P7 Pinned C comparison | NOT STARTED | Standalone C microkernels and recursive apply harness | Numerically matched one-thread comparisons |
| P8 Large qualification | NOT STARTED | 1–32-thread scaling, large worker–firm graphs, memory ceilings | Documented production recommendations |
| P9 Advanced optimization | DEFERRED | Panel RHS kernels, explicit SIMD, NUMA tuning, alternative CG variants | Only after profiles justify complexity |

## Approved architecture

- Rayon will be optional, with a retained serial build.
- Parallel execution will use a package-owned custom thread pool.
- Build-time canonical edges and solve-time deterministic CSR will be separate representations.
- Parallel kernels will use gather form rather than atomics or thread-local full output vectors.
- Scheduling across RHSs and within a solve will respect an explicit memory budget.
- Compact 32-bit internal vertex indices may be used when dimensions permit.
- Pinned upstream C sources may be vendored only under benchmark/test infrastructure with attribution.

## Implemented performance changes

### P1: repeated-solve overhead

- `Laplacian::clone` preserves a private lineage token, making the normal preconditioner/graph compatibility check constant-time. Independently rebuilt equal graphs retain the structural fallback.
- PCG reuses the finest `Components` object owned by the preconditioner instead of rebuilding union-find state for every RHS.
- Component projection and centering use persistent scratch in `PcgWorkspace` and at every CMG hierarchy level.
- Workspace types expose retained principal heap-byte counts for future memory-aware scheduling.

### P2: terminal and hierarchy scratch

- The trusted dense terminal factorization, ordering, pivots, and repeat-count denominator remain unchanged.
- After factorization, the strict lower factor is frozen into either packed-triangular storage or a sparse row/column traversal representation, selected by retained-byte cost.
- Sparse terminal indices use `u32` when selected; the packed fallback covers dimensions outside that range.
- Forward and backward substitution no longer scan a nested dense `Vec<Vec<f64>>` containing structural zeros when sparse storage is smaller.
- Each nonterminal CMG level aliases the matvec and residual roles through one vector, removing one full vector per hierarchy level.
- `GroundedLdl::byte_len`, `CmgWorkspace::byte_len`, and `PcgWorkspace::byte_len` provide direct memory accounting.
- Commit `daa677b95f0bf23310e941846f5e3b105274597e` passed debug and release tests on Ubuntu, macOS, and Windows. Its only quality failure was pre-format source; `fd8db573a7a5d4c5f9b0dde4fc2a7d103499b6db` contains the formatter output.

## Remaining observed hot spots

- edge-scatter matvec prevents efficient parallel row ownership;
- batch solving remains sequential;
- coarse contraction repeatedly allocates and sorts endpoint triples;
- forest splitting contains recurring temporary-vector allocation;
- terminal factorization itself still materializes dense temporary matrices during setup.

## Checkpoint log

| Date | Commit | Result | Next action |
|---|---|---|---|
| 2026-08-22 | `b65ae28a15f00925348046bb474c8133e5128cd0` | Correctness-qualified baseline identified | Commit measurement infrastructure |
| 2026-08-22 | `cc9d641d9e4d227196389eb59144e762d3caa926` | Benchmark driver, live plan, and performance workflow added | Cross-platform qualification |
| 2026-08-22 | `b45b252f88925028e3ad9a73a3f75eeab05f6754` | Benchmark source formatted and built on all three platforms | Remove repeated solve setup |
| 2026-08-22 | `703ddc2e809f5c92f8a6acbec79b0b7b31a763f0` | Graph lineage and cached components passed full CI | Remove component allocations |
| 2026-08-22 | `a6b339fdb86f137d3ad75891922f9ad1fd3f8d3c` | Reusable component scratch passed all platform tests | Compress terminal and workspace storage |
| 2026-08-22 | `daa677b95f0bf23310e941846f5e3b105274597e` | P2 passed debug/release tests on all platforms; formatted in `fd8db573` | Persist matched A/B timing and begin CSR |

## Current next action

Run and retain the first matched baseline/candidate comparison. Then implement a serial deterministic CSR operator, first as a measured alternative that coexists with canonical edges. Add exact edge-versus-CSR matvec tests and a storage/crossover report before routing production CMG levels through CSR.