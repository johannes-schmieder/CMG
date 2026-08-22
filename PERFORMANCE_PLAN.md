# CMG Performance Optimization Plan

This document is the live recovery and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baseline

- Correctness-qualified starting commit: `b65ae28a15f00925348046bb474c8133e5128cd0`
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

The benchmark driver reports input generation, graph construction, hierarchy construction, one CMG application, certified PCG solves, iteration counts, backward error, hierarchy structure, and storage estimates. Candidate and baseline builds must use the same toolchain and release settings. Hosted-runner timings are directional evidence; large-scale and 32-core claims require a larger or self-hosted runner.

## Workloads

Graph families include paths, grids, bottleneck graphs, heavy-tailed bipartite worker–firm graphs, duplicate edges, heterogeneous weights, and deterministic synthetic random graphs.

Target sizes range from 10,000-edge CI smoke tests through 10-million-edge dedicated runs. Shared-hierarchy workloads use 1, 4, 8, 32, and 128 right-hand sides. Thread counts are 1, 2, 4, 8, 16, 32, and higher where hardware permits.

## Phases

| Phase | Status | Scope | Gate |
|---|---|---|---|
| P0 Measurement | COMPLETE | Benchmark driver, workflow, baseline record, memory estimates | Benchmark binary builds on all platforms and emits JSON |
| P1 Remove repeated overhead | IN VALIDATION | Cache components and graph identity; remove recurring component allocations | Same solutions and iterations; serial repeated-RHS improvement |
| P2 Terminal and workspace memory | NOT STARTED | Compress terminal traversal; alias safe scratch arrays; expose byte estimates | Lower apply time and workspace bytes |
| P3 Frozen CSR operator | NOT STARTED | Deterministic row representation and grouped aggregate/component membership | Serial CSR correctness and measured crossover policy |
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

### Constant-time normal graph compatibility

`Laplacian::clone` now preserves a private lineage token. A preconditioner built from the graph therefore recognizes the normal solve path in constant time. A separately reconstructed but structurally equal graph still works through the previous full comparison fallback.

### Cached connected components

PCG now reuses the finest-level `Components` object already built and owned by `CmgPreconditioner`; it no longer runs union-find for every right-hand side.

### Reusable component scratch

Component compatibility projection and centering now have allocation-free internal paths backed by reusable scratch vectors. `PcgWorkspace` owns finest-level scratch, and `CmgWorkspace` owns one scratch object per hierarchy level. Public convenience methods retain their previous allocating behavior for compatibility.

The workspace types now report their reserved principal heap bytes, allowing the future multi-RHS scheduler to enforce a memory budget rather than estimating concurrency from vertex count alone.

## Remaining observed hot spots

- edge-scatter matvec prevents efficient parallel row ownership;
- the terminal solve stores and scans a dense lower factor;
- CMG keeps separate same-dimension matvec and residual arrays;
- batch solving remains sequential;
- coarse contraction repeatedly allocates and sorts endpoint triples;
- forest splitting contains recurring temporary-vector allocation.

## Checkpoint log

| Date | Commit | Result | Next action |
|---|---|---|---|
| 2026-08-22 | `b65ae28a15f00925348046bb474c8133e5128cd0` | Correctness-qualified baseline identified | Commit measurement infrastructure |
| 2026-08-22 | `cc9d641d9e4d227196389eb59144e762d3caa926` | Benchmark driver, live plan, and performance workflow added | Cross-platform qualification |
| 2026-08-22 | `b45b252f88925028e3ad9a73a3f75eeab05f6754` | Benchmark source formatted and built on all three platforms | Remove repeated solve setup |
| 2026-08-22 | `703ddc2e809f5c92f8a6acbec79b0b7b31a763f0` | Graph lineage and cached components passed quality plus debug/release tests on Linux, macOS, and Windows | Remove component allocations |

## Current next action

Validate the reusable component-workspace checkpoint. If it is green, record P1 as complete, update benchmark memory reporting to use exact workspace byte counts, and begin P2 with terminal-factor compression and CMG scratch aliasing.