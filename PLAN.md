# CMG Rust Port — Live Implementation Plan

Last updated: 2026-08-22

## 1. Goal

Create a deterministic, production-oriented Rust port of the stationary
Combinatorial Multigrid (CMG) preconditioner published by Koutis, Miller, and
Tolliver and implemented in the official `ikoutis/cmg-solver` repository.
Version 0.1 covers hierarchy construction, stationary recursive application,
SDDM augmentation, a component-aware direct terminal, certified PCG, repeated
right-hand sides, diagnostics, and exact small-problem qualification.

## 2. Pinned upstream

- repository: `ikoutis/cmg-solver`
- commit: `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- commit date: 2026-07-05
- license: GNU GPL version 3

The pinned revision includes the corrected C forest-component size
initialization. `UPSTREAM.md` records routine-by-routine coverage and deliberate
Rust extensions.

## 3. Definition of complete

- [x] strict input validation for weighted Laplacians and SDDM matrices;
- [x] deterministic edge canonicalization and duplicate aggregation;
- [x] connected components and Laplacian null-space compatibility checks;
- [x] exact SDDM-to-Laplacian augmentation and solution extraction;
- [x] maximum-weight incident-edge forest construction;
- [x] faithful forest diameter/conductance splitting;
- [x] low-effective-degree forest correction;
- [x] deterministic forest-component labeling;
- [x] exact Galerkin coarse-graph contraction;
- [x] upstream hierarchy terminal and stagnation rules;
- [x] upstream nonzero-ratio recursive repeat schedule, including the direct
      terminal's grounded unit-lower-factor nonzero count;
- [x] grounded degree-ordered LDL^T terminal solver;
- [x] stationary recursive CMG cycle with damped Jacobi smoothing;
- [x] reusable immutable preconditioner and caller-owned workspaces;
- [x] PCG with fresh original-system residual certification;
- [x] repeated-right-hand-side APIs for Laplacian and SDDM systems;
- [x] reusable end-to-end SDDM solver with original-matrix certification;
- [x] independent allocating stationary-cycle oracle;
- [x] dense small-system solution oracle;
- [x] determinism tests under input permutation and duplicate splitting;
- [ ] derived-value overflow and non-finite propagation hardening;
- [ ] final adversarial matrix green on Linux, macOS, and Windows;
- [ ] final source-coverage and documentation audit.

## 4. Architecture

```text
src/
  lib.rs              Public exports
  error.rs            Typed validation/build/apply/solve errors
  options.rs          Validation, CMG, and PCG options
  graph.rs            Canonical weighted undirected Laplacian
  sddm.rs             SDDM validation and exact augmentation
  components.rs       Connected components and quotient-space operations
  forest.rs           Heavy-edge forest, splitting, and forest components
  coarsen.rs          Restriction, prolongation, and Galerkin contraction
  hierarchy.rs        Hierarchy construction, stops, repeats, diagnostics
  ldl.rs              Component-grounded terminal LDL^T
  workspace.rs        Reusable preconditioner workspaces
  preconditioner.rs   Stationary recursive CMG cycle
  pcg.rs              Certified PCG and batched solves
  sddm_solver.rs      End-to-end reusable SDDM solver

tests/
  graph_exact.rs
  sddm_small.rs
  forest_exact.rs
  hierarchy_exact.rs
  ldl_small.rs
  preconditioner_properties.rs
  solve_small.rs
  sddm_solve.rs
  adversarial.rs
  dense_reference.rs
  determinism.rs
  upstream_cycle.rs
```

The v0.1 numerical core has no runtime dependencies. It is safe,
single-threaded Rust and deterministic. Parallelism and platform-specific
optimization are deferred until the stationary reference implementation is
fully qualified.

## 5. Milestones

| Phase | Status | Gate |
|---|---|---|
| 0. Contract, provenance, CI | **complete** | Cross-platform quality/test harness established |
| 1. Graph and SDDM core | **complete** | Dense assembly, energy, components, and exact augmentation tested |
| 2. Forest decomposition | **complete** | Heavy-edge, split-kernel, low-degree, and component tests green |
| 3. Coarse graphs and hierarchy | **complete** | Dense `R L R^T`, all stop modes, and repeat schedule tested |
| 4. Terminal LDL^T | **complete** | Connected/disconnected direct solves and residual tests green |
| 5. CMG cycle | **complete** | Linearity, symmetry, positivity, workspace, and reference-cycle tests green |
| 6. PCG, batching, and SDDM wrapper | **complete** | Fresh certificates, batches, adaptive SDDM refinement tested |
| 7. Adversarial qualification | **in progress** | Overflow, extreme-scale, graph-family, and cross-platform matrix green |
| 8. Completion audit and docs | **in progress** | Every upstream production routine mapped and final docs accurate |

## 6. Implemented numerical path

The current code provides:

- canonical sorted undirected edges with deterministic duplicate summation;
- weighted degrees, sparse matrix-vector products, dense diagnostic assembly,
  matrix nonzero counts, and the graph energy identity;
- deterministic connected components including isolated vertices;
- component-wise compatibility validation, exact quotient-space projection,
  and component centering;
- dense and sparse SDDM validation with typed errors;
- exact extra-vertex augmentation for every positive dominance excess;
- right-hand-side lifting and gauge-invariant SDDM solution extraction;
- deterministic maximum-weight incident-edge selection with explicit tie rules;
- a Rust port of the pinned C forest diameter/conductance splitter;
- upstream low-effective-degree correction and deterministic aggregate labels;
- restriction, prolongation, and exact edge-based construction of `R L R^T`;
- direct, full-contraction, vertex-stagnation, fill-stagnation, and
  maximum-level terminals with explicit diagnostics;
- one deterministic highest-index anchor per connected component;
- static grounded-row nonzero ordering and no-pivot LDL^T factorization;
- exact unit-lower-factor-nonzero calibration of the final recursive repeat;
- stationary damped-Jacobi pre/post smoothing, residual restriction, recursive
  coarse correction, prolongation, and configured repeat cycles;
- immutable preconditioners and reusable caller-owned per-level work arrays;
- quotient-space PCG with typed breakdown and iteration-limit errors;
- periodic residual replacement and immediate fresh residual verification at
  candidate convergence;
- original, unprojected right-hand-side certification after every solve;
- relative residual, normwise backward error, tolerance, iterations, restarts,
  and SDDM refinement counts;
- sequential batched right-hand-side solves with one hierarchy and workspace;
- an end-to-end SDDM solver that certifies the extracted solution against the
  original SDDM matrix and tightens only the augmented solve when needed.

## 7. Qualification matrix

Implemented graph and matrix families include:

- paths, cycles, stars, trees, complete graphs, grids, barbells, and lollipops;
- bipartite worker-firm-style graphs;
- disjoint unions and isolated vertices;
- strictly dominant SDDM matrices and singular Laplacian SDDM blocks;
- equal, heterogeneous, weak-bridge, and multi-order-of-magnitude weights;
- duplicated, reversed, and permuted edge lists.

Implemented invariants and independent checks include:

- `x^T L x = sum_e w_e (x_u - x_v)^2`;
- exact dense assembly and sparse matvec agreement;
- exact dense Galerkin equality;
- component-wise range compatibility and deterministic projection;
- heavy-edge tie behavior and pinned forest-split vectors;
- every hierarchy stopping mode and strict nonterminal reduction;
- direct-factor residuals on connected and disconnected systems;
- direct-terminal repeat calibration from `nnz(L)` of the grounded LDL factor;
- preconditioner linearity, numerical symmetry, and positive action;
- independent allocating reproduction of the stationary recursive cycle;
- dense-reference solutions for small compatible systems;
- fresh residual and backward-error certificates;
- individual, reusable-workspace, and batched result equality;
- hierarchy and solve invariance under edge permutation and duplicate splitting;
- explicit rejection of incompatible RHSs, PCG breakdown, and exhausted
  iteration budgets.

Remaining qualification work concentrates on non-finite derived quantities,
near-overflow inputs, empty/invalid aggregate maps, and the final full matrix on
all three operating systems.

## 8. Deliberate Rust extensions and interface differences

- Rust supports small matrices instead of refusing inputs below 500 vertices.
- Rust uses zero-based indices and typed errors instead of flags and warnings.
- Rust handles disconnected Laplacians component by component.
- Tie-breaking and aggregation order are explicitly deterministic.
- Every positive SDDM dominance excess is augmented exactly, including values
  below the MATLAB wrapper's numerical classification threshold.
- A compatible floating-point RHS is projected exactly onto each component's
  Laplacian range before the stationary cycle, while final acceptance remains
  against the original, unprojected RHS.
- Rust exposes hierarchy diagnostics and certified solve results directly.
- A configurable maximum-level guard is added as a hard safety limit.
- The direct terminal grounds one vertex per component rather than assuming one
  connected graph with one final coordinate.
- PCG periodically replaces its recursive residual and always verifies
  convergence from a fresh original-system matvec.
- The SDDM wrapper may rerun the augmented solve at a stricter absolute target
  when the original SDDM certificate is tighter; it never relaxes the requested
  original-system tolerance.

The hierarchy constants and stationary cycle remain faithful to the pinned
upstream implementation unless a deviation is recorded here and in
`UPSTREAM.md`.

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-22 | Pin upstream commit `19752fc...` | Stable target including corrected forest-size initialization |
| 2026-08-22 | GPL-3.0-only | Preserve source-derived upstream licensing |
| 2026-08-22 | No numerical runtime dependencies for v0.1 | Auditable and portable kernels |
| 2026-08-22 | Develop on `main` with checkpoint commits | Recoverability if the thread ends |
| 2026-08-22 | Deterministic, single-threaded first | Correctness before parallel optimization |
| 2026-08-22 | Augment every positive SDDM excess | Preserve the supplied matrix exactly |
| 2026-08-22 | Lowest-neighbor heavy-edge tie rule | Stable behavior matching sparse index order |
| 2026-08-22 | Ground the highest-index vertex per component | Deterministic disconnected extension of upstream gauge |
| 2026-08-22 | Dense terminal factor for v0.1 | Auditability first; terminal size is bounded by configuration |
| 2026-08-22 | Reusable workspaces via take/restore | Avoid application-time allocation without unsafe aliasing |
| 2026-08-22 | Fresh residual certification and periodic restarts | Prevent false convergence from accumulated residual drift |
| 2026-08-22 | Compatibility projection near roundoff scale | Permit cancellation noise but retain original-RHS certification |
| 2026-08-22 | Publish post-LDL repeat in hierarchy diagnostics | Match upstream's final hierarchy state as well as executed cycle |

## 10. Current risks and open defects

- Degree, row-sum, norm, and tolerance calculations still require explicit
  non-finite-derived-value checks to prevent overflow from becoming a false
  success or an opaque later failure.
- Dense LDL setup is cubic and can be costly for unusually dense terminal
  levels; sparse factorization is a future optimization, not a v0.1 correctness
  dependency.
- The upstream hierarchy can stagnate on dense graphs. The Rust port preserves
  the iterative terminal fallback and reports it explicitly.
- The repository currently retains a temporary automatic-rustfmt workflow used
  because Rust is unavailable in the agent container; it will be removed after
  the final formatted checkpoint.
- The license file must be replaced with the complete GPLv3 text before the
  completion audit closes.
- Large-scale performance and memory qualification are deliberately deferred;
  v0.1 qualification is correctness-focused on small and adversarial systems.

## 11. Checkpoint log

| Checkpoint | Commit | CI | Notes |
|---|---|---|---|
| Phase 0 quality harness | `bb5d56e` | green | format, Clippy, docs, debug/release tests and builds on three OSes |
| Graph and exact SDDM foundation | `cfd4d073`–`9fa8af0b` | green | graph, components, validation, augmentation |
| Forest and hierarchy | `a5b80c9b`–`5b0a399b` | green | split kernel, grouping, Galerkin, stopping rules |
| Terminal LDL^T and CMG cycle | `5abf4ed6`–`2184ff5d` | green | factorization, recursion, workspaces |
| Certified PCG and batching | `62224219`–`9262e7a5` | green | certificates, diagnostics, batches, errors |
| Reusable SDDM wrapper | `6af3bd4e`–`6ac224b6` | green after refinement fix | original-matrix certification and batches |
| Independent cycle/determinism/adversarial suite | `c2fa5420` | green | quality and debug/release matrix on Linux/macOS/Windows |
| Direct-terminal repeat diagnostic regression | `bd01b0b9`–`02c296ce` | matrix running | publish the calibrated upstream repeat in hierarchy diagnostics |

## 12. Current next action

Finish the direct-terminal repeat regression matrix, then harden all derived
floating-point quantities against overflow and non-finite propagation. After
that, run the complete adversarial suite on Linux, macOS, and Windows, replace
the license with the full GPLv3 text, update `UPSTREAM.md` and `README.md`, remove
the temporary formatting workflow, and close the completion audit.
