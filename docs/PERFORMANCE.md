# Performance and parallel execution

This is the maintained performance reference for the crate. Detailed one-shot optimization experiments are preserved in Git history and GitHub Actions artifacts; the current tree keeps only durable benchmark harnesses and a small set of latest machine-readable records.

## Retained implementation

The production implementation includes compact canonical edge storage, shared Laplacian hierarchy ownership, compact aggregation/CSR indices where qualified, compressed terminal factors, reduced CMG/PCG workspaces, cached endpoint keys, optimized forest splitting and contraction, row-owned parallel construction of dense planned operators, fixed-order parallel centering and norm reductions, a package-owned Rayon executor, selectively routed within-solve parallelism, memory-bounded concurrency across right-hand sides, and final original-system residual certification.

## SCC Rust versus official MATLAB/C study

The controlled large-scale study uses Intel Xeon Gold 6242 nodes, Rust 1.98.0, MATLAB 2026a with its default C MEX compiler, and the official CMG source pinned at `19752fc102f8cae8e34f66457bfaccb1aaa60375`. Five deterministic connected graph families are measured at 100,000, 300,000, and 1,000,000 vertices with 1, 2, 4, 8, 16, and 32 application CPUs. Every primary timing is the median of three repetitions after one warm-up.

At 32 CPUs, the geometric-mean Rust/MATLAB ratios across the 15 graph/size cases are:

| Metric | Ratio | Interpretation |
|---|---:|---|
| preconditioner setup | 0.265x | Rust setup takes about 27% of MATLAB time |
| stationary CMG application | 0.790x | Rust takes about 79% of MATLAB time |
| reused-preconditioner PCG | 0.783x | Rust takes about 78% of MATLAB time |
| setup plus one solve | 0.666x | Rust takes about two-thirds of MATLAB time |
| process peak RSS | 0.150x | Rust uses about one-sixth of MATLAB process RSS |

Rust is faster in setup plus solve for all five one-million-vertex families. The smallest gap is the weighted path (2.96 seconds versus 3.32 seconds); the largest is dense worker-firm degree 16 (6.73 seconds versus 14.8 seconds). The geometric-mean full-workflow speedup from 1 to 32 CPUs at one million vertices is only 1.05x for Rust and 1.15x for MATLAB, with nonmonotone family-level curves. More allocated CPUs therefore do not translate into proportional end-to-end gains when sequential setup and parallel overhead dominate.

The 16-RHS supplement provides an important contrast. Across sparse and dense worker-firm graphs at 300,000 and 1,000,000 vertices, Rust's memory-bounded across-RHS executor has a 7.76x geometric-mean 1-to-32-CPU speedup, while sequential MATLAB PCG has 1.03x. At 32 CPUs, Rust's geometric-mean normalized per-RHS time is 0.122x MATLAB's. The four individual Rust speedups range from 6.63x to 9.89x. Coarse-grained independent-RHS concurrency is therefore substantially more effective on this machine than the selectively parallelized single-RHS workflow.

All 180 main and 48 batch implementation/thread points pass the scheduler, identity, timing, memory, and numerical validator. The maximum Rust backward error is `9.95e-9`; MATLAB's maximum native reported relative residual is below `1e-8`. Independently recomputed diagnostics are retained because the two packages preserve different native stopping rules. Twelve of 15 main graph/size cases have matching intermediate hierarchy sizes but a one-vertex difference in the final coarse level; the three dense cases match exactly. The official MATLAB hierarchy reports its nonzero status flag for the dense cases, which remain included and explicitly marked.

The original main archive is immutable. Its Rust and standalone-C JSON rows recorded `source_commit=unknown` because the wrapper and compiled benchmark driver used different environment-variable names. A derived copy repairs only that field in 96 files from the exact source manifest; its receipt records the raw-tree digest and all before/after hashes. No timing, numerical, input, or environment value is changed. The strengthened validator accepts the derived run, and the report discloses this bookkeeping correction.

The complete report, figures, compact record, and reproduction tooling live in `benchmarks/report/`, `.ci/performance/scc-latest.json`, and `benchmarks/README.md`. Raw repetitions, logs, resource receipts, and SGE accounting remain in the isolated run archive.

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

`benchmarks/c-kernel/` checks pinned upstream C sparse kernels against Rust. In the SCC study, Rust/C SpMV ratios range from 0.666x to 0.915x, restriction and prolongation are near parity, and the bounded recursive-cycle ratios range from 0.737x to 0.803x. This is direct hot-kernel evidence, not an end-to-end C-solver comparison, because upstream hierarchy construction is primarily MATLAB code.

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

The SCC study now supplies controlled 32-core evidence for the synthetic graph matrix described above. Ordinary hosted-runner results and extrapolations beyond the measured SCC environment should still be described as directional only.
