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

The benchmark driver reports separately:

- input edge generation;
- canonical graph construction;
- CMG hierarchy and terminal construction;
- one stationary preconditioner application;
- certified PCG solves;
- PCG iterations and final backward error;
- hierarchy vertex and nonzero counts;
- approximate graph and hierarchy storage.

Candidate and baseline builds should be compiled with the same toolchain and release settings. Hosted-runner timings are treated as directional evidence, not as stable regression thresholds. Large-scale and 32-core claims require a larger or self-hosted runner.

## Workloads

Graph families:

- path and grid graphs;
- weakly connected and bottleneck graphs;
- heavy-tailed bipartite worker–firm graphs;
- duplicate-edge and heterogeneous-weight variants;
- deterministic synthetic random graphs.

Target sizes:

- CI smoke: roughly 10,000–50,000 edges;
- routine macrobenchmarks: 100,000–1,000,000 edges;
- dedicated qualification: 10,000,000 or more edges, subject to runner memory.

RHS workloads: 1, 4, 8, 32, and 128 shared-hierarchy systems.

Thread counts: 1, 2, 4, 8, 16, 32, and higher where hardware permits.

## Phases

| Phase | Status | Scope | Gate |
|---|---|---|---|
| P0 Measurement | IN PROGRESS | Benchmark driver, performance workflow, baseline record, memory estimates | Baseline compiles, tests, and emits machine-readable output |
| P1 Remove repeated overhead | NOT STARTED | Cache components and graph identity; remove per-solve reconstruction and recurring allocations | Same solutions and iterations; serial repeated-RHS improvement |
| P2 Terminal and workspace memory | NOT STARTED | Compress terminal traversal; alias safe scratch arrays; expose byte estimates | Lower apply time and workspace bytes |
| P3 Frozen CSR operator | NOT STARTED | Deterministic row representation and grouped aggregate/component membership | Serial CSR correctness and measured crossover policy |
| P4 Parallel solve kernels | NOT STARTED | Package-owned thread pool; parallel matvec, vector updates, restriction, prolongation, reductions | Equivalent certified answers across thread counts |
| P5 Multi-RHS scheduler | NOT STARTED | Memory-aware concurrency across independent solves | Throughput scaling without uncontrolled memory growth |
| P6 Parallel setup | NOT STARTED | Parallel canonicalization, contraction, sorting, and heavy-edge selection | Preserved hierarchy diagnostics and faster setup |
| P7 Pinned C comparison | NOT STARTED | Standalone C microkernels and recursive apply harness | Numerically matched one-thread comparisons |
| P8 Large qualification | NOT STARTED | 1–32-thread scaling, large worker–firm graphs, memory ceilings | Documented production recommendations |
| P9 Advanced optimization | DEFERRED | Panel RHS kernels, explicit SIMD, NUMA tuning, alternative CG variants | Only after profiles justify complexity |

## Approved architecture

- Add Rayon as an optional feature rather than a mandatory runtime dependency.
- Use a package-owned custom thread pool; never silently claim the process-wide global pool.
- Retain a serial build and serial execution path.
- Introduce a solve-oriented deterministic CSR representation while retaining compact build-time edges.
- Prefer gather-style parallel kernels over atomics or per-thread full output vectors.
- Use memory-aware hybrid scheduling across RHSs and within a solve.
- Permit compact 32-bit internal vertex indices when the graph fits, while public dimensions and edge counts remain `usize`.
- Vendor pinned upstream C sources only under benchmark/test infrastructure with attribution.

## Current observations

The baseline repeats avoidable work in repeated solves:

- finest-graph structural equality scans;
- connected-component reconstruction;
- component projection and centering allocations;
- sequential batch processing.

The baseline edge-scatter matvec is compact and effective serially but is not an appropriate multicore kernel because two endpoints are updated for each edge. The primary parallel representation will therefore use deterministic row-wise CSR.

The terminal dimension is bounded by the configured direct threshold, but the baseline stores and traverses a dense lower factor. The initial terminal optimization will keep the trusted dense factorization and compress the completed factor for repeated triangular solves.

## Checkpoint log

| Date | Commit | Result | Next action |
|---|---|---|---|
| 2026-08-22 | `b65ae28a15f00925348046bb474c8133e5128cd0` | Correctness-qualified baseline identified | Commit measurement-only infrastructure |

## Current next action

Commit and validate P0 without changing numerical solver behavior. Once baseline artifacts are available, begin P1 by caching finest-level component metadata and replacing full structural graph comparison with a constant-time preconditioner identity check.