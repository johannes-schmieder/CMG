# Performance and parallel execution

This is the maintained performance reference for the crate. Detailed one-shot optimization experiments are preserved in Git history and GitHub Actions artifacts; the current tree keeps only durable benchmark harnesses and a small set of latest machine-readable records.

## Retained implementation

The production implementation includes compact canonical edge storage, shared Laplacian hierarchy ownership, compact aggregation/CSR indices where qualified, compressed terminal factors, reduced CMG/PCG workspaces, cached endpoint keys, optimized forest splitting and contraction, a package-owned Rayon executor, selectively routed within-solve parallelism, memory-bounded concurrency across right-hand sides, deterministic reductions, and final original-system residual certification.

## Cumulative checkpoint

`.ci/performance/cumulative-latest.json` compares numerical checkpoint `f50cbd52734ad84af39131c12ad5dae181d8c7b5` with the frozen early Rust baseline `b45b252f88925028e3ad9a73a3f75eeab05f6754`. Later retained changes are not included, so this is a reproducible checkpoint rather than an exact current-head benchmark.

Geometric current/baseline ratios were:

| Metric | Ratio | Interpretation |
|---|---:|---|
| graph construction | 0.802x | about 20% faster |
| hierarchy construction | 0.721x | about 28% faster |
| stationary CMG application | 0.225x | about 4.4x faster |
| solve per RHS | 0.372x | about 2.7x faster |
| graph core bytes | 0.735x | about 27% less |
| hierarchy core bytes | 0.770x | about 23% less |
| CMG workspace bytes | 0.460x | about 54% less |
| PCG workspace bytes | 0.614x | about 39% less |

Individual cases vary; the JSON record contains the complete measurements.

## Direct comparison with pinned C kernels

`benchmarks/c-kernel/` checks pinned upstream C sparse kernels against Rust. The latest durable record is `.ci/performance/c-kernel-latest.json`. It provides direct hot-kernel evidence, not an end-to-end MATLAB comparison, because upstream hierarchy construction is primarily MATLAB code.

## Parallel routing

`ParallelPcgSolver` chooses among serial PCG, selectively planned within-solve PCG, and independent solves distributed across right-hand sides.

The default single-RHS planned threshold is **350,000 canonical retained edges**, provided more than one worker thread and at least one routed operator are available. This is a measured performance heuristic, not a mathematical CMG constant, and can be overridden through `ParallelPcgPolicy`.

The latest hosted routing matrix is `.ci/performance/full-pcg-routing-latest.json`; the largest dense worker-firm case in that record reports roughly 2.2x planned-versus-serial full-PCG speedup with unchanged iteration count and solution certificate. `.ci/performance/parallel-latest.json` records repeated-RHS thread scaling.

## Memory budgeting

Let `W = solver.workspace_bytes()` and let `B` be the configured reusable workspace budget. Batch concurrency is bounded approximately by:

```text
min(thread_count, rhs_count, floor(B / W))
```

The budget excludes the shared graph, hierarchy, optional parallel plan, input RHSs, result vectors, allocator overhead, and host-process memory. Large machines should not assume that one full PCG workspace per CPU is affordable.

## Current bottleneck

After the retained forest, storage, and solve improvements, hierarchy setup is still dominated by coarse contraction, and contraction is dominated by endpoint sorting. Several bucket/radix alternatives were benchmarked and rejected because their end-to-end or peak-memory tradeoffs were worse than the retained cached-key comparison sort. Further setup changes should therefore require a clear end-to-end gain on large worker-firm graphs with bounded peak memory.

## Durable machine-readable records

The maintained `.ci/performance/` directory contains only:

- `latest.json` — frozen-baseline performance workflow result;
- `full-pcg-routing-latest.json` — serial versus planned certified PCG matrix;
- `parallel-latest.json` — hosted-runner thread scaling;
- `c-kernel-latest.json` — pinned C/Rust differential comparison;
- `cumulative-latest.json` — frozen cumulative optimization checkpoint.

Older experiment records are available from Git history. Raw logs and temporary benchmark details belong in Actions artifacts, not the current source tree.

## Benchmark discipline

1. Keep the production baseline untouched.
2. Build baseline and candidate with the same compiler, features, and CPU settings.
3. Use deterministic identical graphs and RHSs.
4. Alternate baseline/candidate measurements and use medians.
5. Verify hierarchy metadata, iteration counts, backward errors, and scaled solution differences before interpreting time.
6. Measure requested allocation and process RSS when memory can change.
7. Retain a change only when its end-to-end benefit justifies its complexity and memory cost.

Durable benchmark binaries live in `benchmarks/`; see `benchmarks/README.md`.

## 8/16/32-core qualification

The library can construct package-owned pools at 1, 2, 4, 8, 16, and 32 threads. That establishes functional support, not scaling performance.

`.github/workflows/manual-32-thread-qualification.yml` is a manually dispatched, read-only workflow for a configured larger runner or controlled self-hosted machine. It records machine topology, compiler versions, numerical agreement, setup/solve timing, iteration counts, hierarchy/workspace bytes, peak RSS, and 1/2/4/8/16/32-thread measurements where hardware permits.

Until controlled large-machine evidence exists, ordinary hosted-runner results should be described as directional only.
