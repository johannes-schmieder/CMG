# CMG Performance Optimization Plan

This is the live recovery, measurement, and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baselines and pinned references

- Correctness baseline: `b65ae28a15f00925348046bb474c8133e5128cd0`
- Frozen benchmark harness/numerical baseline: `b45b252f88925028e3ad9a73a3f75eeab05f6754`
- Pinned official CMG source: `19752fc102f8cae8e34f66457bfaccb1aaa60375`

Retained records:

- `.ci/latest.json`: format, Clippy, rustdoc, debug/release, and three-platform qualification.
- `.ci/performance/latest.json`: same-run frozen-baseline versus current serial benchmarks.
- `.ci/performance/parallel-latest.json`: hosted thread scaling and memory-bounded batch results.
- `.ci/performance/c-kernel-latest.json`: pinned standalone C kernel comparison.
- `.ci/performance/cycle-wiring-latest.json`: complete iterative stationary-cycle differential result.
- `.ci/performance/compatible-apply-latest.json`: accepted compatible-RHS stationary-core experiment.

Hosted-runner timings are directional. Claims about 8–32-thread scaling, NUMA behavior, or very large memory configurations require a larger or self-hosted runner.

## Goals

1. Reduce hierarchy setup and repeated-solve time without weakening original-system residual certification.
2. Scale useful work across machines with 1–32 or more logical CPUs.
3. Keep hierarchy and per-RHS workspace memory predictable on very large graphs.
4. Preserve deterministic hierarchy construction and reproducible numerical behavior.
5. Optimize both one very large solve and many right-hand sides sharing one hierarchy.
6. Compare Rust hot paths with the pinned official C implementation wherever standalone comparison is possible.

## Numerical rules

- No hidden ridge, graph mutation, tolerance relaxation, or silent solver substitution.
- Public-boundary validation and final original-system residual certification remain mandatory.
- Every retained numerical change must pass serial and all-feature debug/release tests.
- Production checkpoints are qualified on Ubuntu, macOS, and Windows.
- Parallel sparse kernels use row/gather ownership rather than atomics or one full output vector per thread.
- The dependency-free serial build remains supported and warning-free.
- Timing is interpreted only after hierarchy diagnostics, iteration counts, C parity where available, and residual certificates are checked.

## Phase status

| Phase | Status | Gate |
|---|---|---|
| P0 Measurement | COMPLETE | Matched baseline/current records are retained |
| P1 Repeated overhead | COMPLETE | Graph metadata and fine-level compatible stationary application are reused |
| P2 Terminal/workspace memory | COMPLETE | Factor compression and scratch aliasing pass qualification |
| P3 Frozen CSR | COMPLETE | Deterministic CSR agrees with edge matvec; measured routing is documented |
| P4 Parallel solve kernels | COMPLETE | Package-owned pool and row-parallel CSR pass cross-platform tests |
| P5 Multi-RHS scheduler | COMPLETE | Ordered memory-bounded concurrent solves pass cross-platform tests |
| P6 Parallel setup | IN PROGRESS | Sorting/contraction/heavy-edge routing is implemented; larger setup profiles remain |
| P7 Pinned C comparison | COMPLETE | Matvec, restriction, prolongation, and full iterative cycle agree and are timed |
| P8 Large qualification | PARTIAL | Hosted 1–4-thread evidence exists; 8–32-thread and high-memory qualification remain |
| P9 Panel/SIMD/NUMA work | DEFERRED | Begin only after profiles justify the complexity |

## Implemented performance work

### Repeated solves and immutable metadata

- Cloned graphs use a private lineage token for constant-time normal compatibility checks, with structural fallback for independently rebuilt equal graphs.
- Connected components for every hierarchy level are computed once and retained by the preconditioner.
- Component projection and centering reuse persistent work arrays.
- `matrix_nnz` and the operator-norm bound are computed once during graph construction and returned in constant time.
- The normal repeated-RHS path no longer reconstructs components or rescans graph diagonals for immutable invariants.
- `CmgPreconditioner::apply_compatible_into` exposes the stationary core for callers that have already established component compatibility.
- PCG projects the submitted RHS once, maintains residuals in the quotient space, removes only accumulated component-nullspace roundoff, and reuses the compatible stationary core on later applications.

The compatible-RHS change was retained at commit `6d5f4cca` after same-run validation of debug/release tests, C-cycle parity, iteration counts, and original-system residual certificates. Relative to the immediately preceding production code:

| Case | Stationary-cycle time | Full PCG solve per RHS | Iterations |
|---|---:|---:|---:|
| Path | `0.697x` | `0.823x` | 26 → 26 |
| Worker–firm | `0.621x` | `0.894x` | 20 → 20 |

Ratios below one favor the new code. Backward errors changed only at floating-point roundoff scale. The full-cycle quotient-space differences from pinned C remained about `2.1e-12` on the path case and `8.9e-16` on worker–firm.

### Terminal and workspace memory

- Completed strict-lower factors select packed-triangular or sparse row/column traversal storage by retained-byte cost.
- Sparse terminal indices use `u32` when valid.
- CMG matvec and residual roles share one full vector per hierarchy level.
- PCG fresh/final residual certification reuses existing storage; per-RHS PCG workspace was reduced from eight to six fine-dimension vectors at commit `f145ac92`.
- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained principal heap bytes.

For the original 20,000-vertex matched run, hierarchy diagnostics, iterations, and backward errors were unchanged. Relative to the frozen baseline, path CMG application and solve time fell substantially, principally because sparse terminal traversal avoided dense lower-triangle scans. Worker–firm workspace memory also declined materially.

### CSR and parallel execution

- `CsrLaplacian` stores deterministic canonical rows and uses compact four-byte neighbor indices when possible.
- Serial production solves retain the compact one-edge-per-undirected-edge scatter kernel because it is faster and smaller on measured cases.
- CSR is used for row-owned parallel matvec without atomics.
- Rayon is optional; `ParallelExecutor` owns an isolated custom pool with an explicit or detected thread count.
- Parallel batch solving shares the immutable hierarchy, uses private reusable workspaces, preserves input order, and caps concurrency using a workspace-memory budget.
- Deterministic parallel edge sorting, coarse contraction, and heavy-edge selection route only above conservative size/density floors.
- Forest splitting reuses traversal storage rather than allocating per leaf walk.

On a four-logical-CPU hosted runner with 50,000 vertices and eight RHSs, retained directional batch throughput is about 2.0x at two threads and 2.5–2.7x at four threads. Across-RHS parallelism remains the preferred route whenever several independent inverse actions are available.

## Pinned C differential qualification

The benchmark-only crate compiles standalone adaptations of the official C loops. It is never linked into the production library. Every timing comparison first verifies numerical agreement.

At 100,000 vertices on a hosted Ubuntu runner, the compact Rust serial edge matvec was faster than the pinned C sparse-symmetric matvec on both retained cases. Restriction and prolongation are numerically exact relative to C and close in speed; results depend on whether labels are contiguous or scattered.

The complete iterative stationary-cycle comparison exposed and repaired an important semantic discrepancy. Official CMG performs one top-level cycle and uses each level's repeat count for the child recursive solve. The Rust production path, both independent Rust references, tests, and documentation use that schedule as of commit `249c2d1f`.

After the compatible stationary-core optimization, same-run complete-cycle timing relative to pinned C was approximately:

| Case | Hierarchy levels | Quotient-space maximum scaled difference | Rust/C median time |
|---|---:|---:|---:|
| Path | 7 | `2.07e-12` | `1.30x` |
| Worker–firm | 8 | `8.88e-16` | `1.46x` |

This is a substantial reduction from the preceding same-run Rust/C ratios of `1.84x` and `2.36x`. Remaining differences are now concentrated inside recursive level application rather than fine-level public compatibility handling.

## Current hot spots

1. Every recursive coarse RHS still uses full public-quality compatibility projection: compatibility and scale accumulation, mean removal, representative search, two correction passes, and projection-norm calculation. Internally generated residuals are compatible in exact arithmetic; deterministic component centering may be sufficient and substantially cheaper, but it must pass symmetry, positivity, adversarial PCG, and C-differential gates.
2. The compatible public method still validates dimensions, workspace structure, and options on every PCG application. A crate-private prevalidated core could remove small repeated checks after the larger recursive-projection issue is measured.
3. Single-RHS production PCG remains mostly serial even when the optional parallel feature is enabled.
4. Coarse contraction still allocates endpoint triples and sorts at every level.
5. Terminal setup materializes dense temporary matrices before retaining compressed factors.
6. Aggregation labels remain native-width `usize`; compact labels could reduce bandwidth, but total hierarchy memory and public API compatibility must be measured before adoption.
7. Hosted hardware has qualified only 1–4 threads.

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
| 2026-08-22 | `6d5f4cca` | Compatible stationary core retained; solve time improved with unchanged iterations |

## Current next action

1. Run fresh three-platform and matched serial/parallel qualification on `6d5f4cca`.
2. Measure deterministic component centering in place of full recursive coarse-RHS compatibility projection, retaining it only if full-cycle parity, PCG convergence, symmetry, positivity, and real solve time improve.
3. If recursive centering is retained, measure a crate-private prevalidated apply path that skips repeated workspace/options checks inside PCG.
4. Continue large setup profiling and obtain 8–32-thread evidence when a suitable runner is available.
5. Remove obsolete one-shot staging workflows and scripts after the active qualification checkpoint is secure.
