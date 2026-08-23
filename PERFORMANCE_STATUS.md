# CMG performance status

This is the concise recovery record for the active optimization phase. Read it together with `PERFORMANCE_PLAN.md` and the machine-readable records in `.ci/performance/` before modifying numerical source.

## Qualified repository state

- Source state entering this documentation refresh: `07f18a672d33a526c61f6d9b7c01114fb953d8db`.
- Latest recorded fully tested SHA: `b9cd7ebdc9dc504f71ec397e1803ef5724d3b771`.
- Recorded quality and Ubuntu/macOS/Windows status: `success` / `success`.
- The serial dependency-free build and the optional `parallel` feature are both maintained.

## Retained implementation

- Deterministic complete stationary CMG hierarchy and certified quotient-space PCG.
- Reused immutable graph/component metadata and allocation-free repeated stationary application.
- Compressed direct terminal factors and reduced CMG/PCG workspaces.
- Checked compact `u32` retained edge endpoints with public `usize` dimensions.
- Trimmed compact graph-build buffers and direct compact coarse contraction.
- Deterministic CSR row operators for selected parallel hierarchy levels.
- Package-owned optional Rayon executor, plan-aware certified PCG, and memory-bounded multi-RHS scheduling.
- Benchmark-only differential comparison against the pinned official C kernels.

## Current evidence

- Direct compact contraction: geometric hierarchy time `0.952x`; geometric peak RSS `0.924x` across large serial/parallel cases.
- Accepted stationary cycle versus pinned C: about `0.866x` on a path case and `1.008x` on a worker–firm case, with quotient-space differences around `2.1e-12` and `1.0e-15`.
- Eight-RHS throughput on the available four-logical-CPU runner: approximately `2.0x` with two threads and `2.5–2.7x` with four threads.
- Selective four-thread stationary application: approximately `2.23x` on dense worker–firm, `1.13x` on a larger sparse worker–firm graph, and no win on smaller/path-like cases.
- These are directional hosted-runner results, not a claim about 8–32-core or NUMA scaling.

## Active recovery point

The direct compact-contraction experiment is resolved and retained. The prepared parallel PCG implementation is present in production source. Stale one-shot compact-index and patch-staging workflows have been removed. The next gate is an end-to-end certified PCG comparison of serial and planned execution, followed by deterministic outer-PCG parallel reductions only if profiling justifies them.

## Remaining major work

- Full-solve routing and threshold qualification.
- Potential parallel PCG reductions/vector kernels.
- Further contraction-buffer/setup profiling.
- Controlled 8-, 16-, and 32-thread/high-memory tests.
- Later panel/SIMD/NUMA work only when supported by profiles.
