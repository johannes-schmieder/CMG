# CMG Performance Optimization Plan

This is the live recovery, measurement, and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baselines and pinned references

- Correctness baseline: `b65ae28a15f00925348046bb474c8133e5128cd0`
- Frozen benchmark harness/numerical baseline: `b45b252f88925028e3ad9a73a3f75eeab05f6754`
- Pinned official CMG source: `19752fc102f8cae8e34f66457bfaccb1aaa60375`

Retained records:

- `.ci/latest.json`: format, Clippy, rustdoc, debug/release and three-platform qualification.
- `.ci/performance/latest.json`: same-run frozen-baseline versus current serial benchmarks.
- `.ci/performance/parallel-latest.json`: hosted thread-scaling and memory-bounded batch results.
- `.ci/performance/c-kernel-latest.json`: pinned standalone C kernel comparison.
- `.ci/performance/cycle-wiring-latest.json`: complete iterative stationary-cycle differential result.

Hosted-runner timings are directional. Claims about 8–32-thread scaling, NUMA behavior, or very large memory configurations require a larger or self-hosted runner.

## Goals

1. Reduce hierarchy setup and repeated-solve time without weakening original-system residual certification.
2. Scale useful work across machines with 1–32 or more logical CPUs.
3. Keep hierarchy and per-RHS workspace memory predictable on very large graphs.
4. Preserve deterministic hierarchy construction and reproducible numerical behavior.
5. Optimize both one very large solve and many right-hand sides sharing one hierarchy.
6. Compare Rust hot paths with the pinned official C implementation wherever a standalone comparison is possible.

## Numerical rules

- No hidden ridge, graph mutation, tolerance relaxation, or silent solver substitution.
- Public-boundary validation and final original-system residual certification remain mandatory.
- Every retained numerical change must pass serial and all-feature debug/release tests.
- Production checkpoints are qualified on Ubuntu, macOS, and Windows.
- Parallel sparse kernels use row/gather ownership rather than atomics or one full output vector per thread.
- The dependency-free serial build remains supported and warning-free.
- Timing is interpreted only after hierarchy diagnostics, iteration counts, and residual certificates are checked.

## Phase status

| Phase | Status | Gate |
|---|---|---|
| P0 Measurement | COMPLETE | Matched baseline/current records are retained |
| P1 Repeated overhead | COMPLETE | Graph lineage, components, and immutable graph invariants are cached |
| P2 Terminal/workspace memory | COMPLETE | Factor compression and scratch aliasing pass qualification |
| P3 Frozen CSR | COMPLETE | Deterministic CSR agrees with edge matvec; measured routing is documented |
| P4 Parallel solve kernels | COMPLETE | Package-owned pool and row-parallel CSR pass cross-platform tests |
| P5 Multi-RHS scheduler | COMPLETE | Ordered memory-bounded concurrent solves pass cross-platform tests |
| P6 Parallel setup | IN PROGRESS | Sorting/contraction/heavy-edge routing is implemented; larger setup profiles remain |
| P7 Pinned C comparison | COMPLETE | Matvec, restriction, prolongation, and full iterative cycle agree numerically and are timed |
| P8 Large qualification | PARTIAL | Hosted 1–4-thread evidence exists; 8–32-thread and high-memory qualification remain |
| P9 Panel/SIMD/NUMA work | DEFERRED | Begin only after profiles justify the complexity |

## Implemented performance work

### Repeated solves and immutable metadata

- Cloned graphs use a private lineage token for constant-time normal compatibility checks, with structural fallback for independently rebuilt equal graphs.
- Connected components for every hierarchy level are computed once and retained by the preconditioner.
- Component projection and centering reuse persistent work arrays.
- `matrix_nnz` and the operator-norm bound are computed once during graph construction and returned in constant time.
- The normal repeated-RHS path no longer reconstructs components or rescans graph diagonals for immutable invariants.

### Terminal and workspace memory

- Completed strict-lower factors select packed-triangular or sparse row/column traversal storage by retained-byte cost.
- Sparse terminal indices use `u32` when valid.
- CMG matvec and residual roles share one full vector per hierarchy level.
- PCG fresh/final residual certification reuses existing storage; per-RHS PCG workspace was reduced from eight to six fine-dimension vectors at commit `f145ac92`.
- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained principal heap bytes.

For the original 20,000-vertex matched run, hierarchy diagnostics, iterations, and backward errors were unchanged. Relative to the frozen baseline, path CMG apply and solve time fell to roughly 0.24x and 0.31x, principally because sparse terminal traversal avoided dense lower-triangle scans. Worker–firm performance remained near parity while workspace memory declined materially.

### CSR and parallel execution

- `CsrLaplacian` stores deterministic canonical rows and uses compact four-byte neighbor indices when possible.
- Serial production solves retain the compact one-edge-per-undirected-edge scatter kernel because it is faster and smaller on measured cases.
- CSR is used for row-owned parallel matvec without atomics.
- Rayon is optional; `ParallelExecutor` owns an isolated custom pool with an explicit or detected thread count.
- Parallel batch solving shares the immutable hierarchy, uses private reusable workspaces, preserves input order, and caps concurrency using a workspace-memory budget.
- Deterministic parallel edge sorting, coarse contraction, and heavy-edge selection route only above conservative size/density floors.
- Forest splitting reuses traversal storage rather than allocating per leaf walk.

On a four-logical-CPU hosted runner with 50,000 vertices and eight RHSs, retained directional batch throughput was about 1.9x at two threads and 2.4–2.7x at four threads. Across-RHS parallelism remains the preferred route whenever several independent inverse actions are available.

## Pinned C differential qualification

The benchmark-only crate compiles standalone adaptations of the official C loops. It is never linked into the production library. Every timing comparison first verifies numerical agreement.

At 100,000 vertices on a hosted Ubuntu runner, the compact Rust serial edge matvec was faster than the pinned C sparse-symmetric matvec on both retained cases. Restriction and prolongation are numerically exact relative to C and close in speed; results depend on whether labels are contiguous or scattered.

The complete iterative stationary-cycle comparison exposed and repaired an important semantic discrepancy. Official CMG performs one top-level cycle and uses each level's repeat count for the child recursive solve. The Rust production path, both independent Rust references, tests, and documentation now use that schedule as of commit `249c2d1f`.

After the repair, the complete Rust and C iterative cycles agree to approximately:

| Case | Hierarchy levels | Quotient-space maximum scaled difference | Rust/C median time |
|---|---:|---:|---:|
| Path | 7 | `2.07e-12` | `1.585x` |
| Worker–firm | 8 | `8.88e-16` | `2.123x` |

The remaining cycle gap is now a measured optimization target rather than an algorithmic ambiguity. The C timing excludes Rust public-boundary compatibility projection and reusable-workspace validation, so the next measurements must separate checked boundary cost from the stationary core before changing arithmetic.

## Current hot spots

1. Public `CmgPreconditioner::apply_into` copies and fully projects the RHS on every application. PCG already maintains a projected quotient-space residual, so an internal prevalidated core path may remove duplicated work without weakening the public API.
2. Every recursive coarse RHS currently uses the full public-quality compatibility projection, including scale validation, representative search, and correction passes. A cheaper linear quotient-space centering step may be sufficient for internally generated residuals, but it must preserve symmetry, positivity, C parity, and certified PCG behavior.
3. Single-RHS production PCG remains mostly serial even when the optional parallel feature is enabled.
4. Coarse contraction still allocates endpoint triples and sorts at every level.
5. Terminal setup materializes dense temporary matrices before retaining compressed factors.
6. Aggregation labels remain native-width `usize`; compact labels could reduce bandwidth, but total hierarchy memory and public API compatibility must be measured before adoption.
7. Hosted hardware has only qualified 1–4 threads.

## Rejected or deferred experiments

- Unsafe unchecked aggregation loops were rejected because the crate globally forbids unsafe production code; safe kernels were retained.
- Duplicating native and compact aggregation labels is not accepted without an end-to-end memory and speed win.
- Pipelined CG, K-cycles, aggressive SIMD, NUMA pinning, and panel Krylov methods remain deferred until ordinary stationary CMG is allocation-free and profiles identify a remaining bottleneck.

## Checkpoint log

| Date | Commit | Result |
|---|---|---|
| 2026-08-22 | `cc9d641d` / `b45b252f` | Measurement infrastructure and frozen baseline established |
| 2026-08-22 | `703ddc2e` | Graph lineage and cached components qualified |
| 2026-08-22 | `a6b339fd` / `8022b568` | Persistent component scratch qualified |
| 2026-08-22 | `daa677b9` / `fd8db573` | Terminal compression and CMG scratch aliasing qualified |
| 2026-08-22 | `31f72e6a` / `365a3572` | CSR crossover measured; serial edge route retained |
| 2026-08-22 | `ffdc96de` / `3cff911e` | Optional custom Rayon pool and deterministic parallel batch solving added |
| 2026-08-22 | `636fa093` / `f17b377d` | Parallel benchmark reconstruction and cross-platform all-feature tests repaired |
| 2026-08-22 | `7e3ab0df` / `fdf5b1de` | Setup routing and forest traversal reuse qualified |
| 2026-08-22 | `31530bcf` / `090048bb` | Pinned C matvec comparison retained |
| 2026-08-22 | `32a49022` / `f536b336` | Pinned C restriction/prolongation comparison retained |
| 2026-08-22 | `f145ac92` | PCG workspace reduced from eight to six fine vectors |
| 2026-08-22 | `deadcb6c` | Immutable graph nonzeros and norm bound cached |
| 2026-08-22 | `249c2d1f` | Recursive repeat schedule aligned with official CMG; full C cycle differential passed |

## Current next action

1. Run fresh three-platform and matched serial/parallel benchmarks on the corrected `249c2d1f` production path.
2. Measure checked public application separately from a prevalidated stationary core.
3. Add a crate-private PCG path that reuses its already projected residual only if same-run benchmarks improve and all quotient-space, symmetry, positivity, adversarial, and cross-platform tests pass.
4. Evaluate replacing recursive full compatibility projection with deterministic component centering for internally generated coarse residuals, again behind a measured accept/reject gate.
5. Continue setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
