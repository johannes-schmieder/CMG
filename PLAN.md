# CMG Rust Port — Live Implementation Plan

Last updated: 2026-08-22

## 1. Goal

Create a deterministic, production-oriented Rust port of the stationary
Combinatorial Multigrid (CMG) preconditioner published by Koutis, Miller, and
Tolliver and implemented in the official `ikoutis/cmg-solver` repository.
The first complete release includes hierarchy construction, the recursive
preconditioner, SDDM augmentation, certified PCG, repeated right-hand sides,
diagnostics, and exact small-problem qualification.

## 2. Pinned upstream

- repository: `ikoutis/cmg-solver`
- commit: `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- commit date: 2026-07-05
- license: GNU GPL version 3

The pinned revision contains the corrected C forest-component size
initialization. `UPSTREAM.md` records routine-by-routine coverage and all
intentional interface or numerical deviations.

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
- [x] final upstream nonzero-ratio recursive repeat schedule, including the
      direct-terminal factor nonzero count;
- [x] grounded degree-ordered LDL^T terminal solver;
- [x] stationary recursive CMG cycle with damped Jacobi smoothing;
- [x] reusable immutable hierarchy and caller-owned workspaces;
- [x] PCG with fresh original-system residual certification;
- [x] repeated-right-hand-side API;
- [ ] exact and adversarial small-problem qualification on Linux, macOS, and
      Windows.

## 4. Architecture

```text
src/
  lib.rs              Public exports
  error.rs            Typed validation/build/apply/solve errors
  options.rs          Validation, CMG, and PCG options
  graph.rs            Canonical weighted undirected Laplacian
  sddm.rs             SDDM validation and exact augmentation
  components.rs       Connected components and null-space operations
  forest.rs           Heavy-edge forest, splitting, and forest components
  coarsen.rs          Restriction, prolongation, and Galerkin contraction
  hierarchy.rs        Hierarchy construction, stops, repeats, diagnostics
  ldl.rs              Component-grounded terminal LDL^T
  workspace.rs        Reusable preconditioner workspaces
  preconditioner.rs   Stationary recursive CMG cycle
  pcg.rs              Certified PCG and batched solves
  sddm_solver.rs      End-to-end SDDM solve wrapper

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
  determinism.rs
```

The initial numerical core has no runtime dependencies. It is single-threaded,
safe Rust and deterministic; parallel optimization is deferred until the
stationary reference port is qualified.

## 5. Milestones

| Phase | Status | Gate |
|---|---|---|
| 0. Contract, provenance, CI | **complete** | Cross-platform quality/test checkpoint green |
| 1. Graph and SDDM core | **complete** | Dense assembly, energy, exact augmentation, and component tests green |
| 2. Forest decomposition | **complete** | Golden parent/split/component tests and quality gates green |
| 3. Coarse graphs and hierarchy | **complete** | Dense `R L R^T`, stop-rule, hierarchy, Clippy, and docs gates green |
| 4. Terminal LDL^T | **complete** | Connected/disconnected direct solves and fresh residual tests green |
| 5. CMG cycle | **complete** | Linearity, symmetry, positivity, recursion, Clippy, and docs gates green |
| 6. PCG and batching | **implemented; formatted quality CI running** | Debug/release tests and builds green on all three operating systems |
| 7. Adversarial qualification | **in progress locally** | SDDM wrapper and expanded graph/weight suite green |
| 8. Completion audit and docs | not started | Every upstream production routine is covered |

## 6. Implemented numerical path

The current code provides:

- canonical sorted undirected edges with deterministic duplicate summation;
- weighted degrees, sparse matrix-vector products, dense diagnostic assembly,
  matrix nonzero counts, and the graph energy identity;
- deterministic connected components including isolated vertices;
- component-wise compatibility validation and centering;
- dense and sparse SDDM validation with typed errors;
- exact extra-vertex augmentation for every positive dominance excess;
- right-hand-side lifting and gauge-invariant SDDM solution extraction;
- deterministic maximum-weight incident-edge selection with explicit tie rules;
- a line-by-line Rust port of the pinned C forest diameter/conductance splitter;
- upstream low-effective-degree correction and deterministic aggregate labels;
- restriction and prolongation through an explicit aggregation map;
- exact edge-based construction of `R L R^T`;
- direct, full-contraction, vertex-stagnation, fill-stagnation, and maximum-level
  terminals with explicit diagnostics;
- one deterministic highest-index anchor per connected component;
- static grounded-row nonzero ordering and no-pivot LDL^T factorization;
- exact factor-nonzero calibration of the final recursive repeat count;
- stationary damped-Jacobi pre/post smoothing, residual restriction, recursive
  coarse correction, and prolongation;
- immutable preconditioners and reusable caller-owned per-level work arrays;
- quotient-space PCG with explicit breakdown and iteration-limit errors;
- periodic residual replacement and immediate fresh residual verification at
  candidate convergence;
- reported relative residual, backward error, tolerance, iterations, and
  restart counts;
- reusable PCG workspaces and sequential batched right-hand-side solves.

The exact augmentation intentionally improves on the MATLAB wrapper's numerical
`1e-13` classification: a small but positive row-sum excess is not discarded or
silently converted to a Laplacian.

## 7. Test matrix

Required graph families include paths, cycles, stars, trees, complete graphs,
grids, barbells, lollipops, bipartite worker-firm graphs, disjoint unions, and
isolated vertices. Tests construct compatible right-hand sides as `b = A x_star`
and verify freshly recomputed original-system residuals.

Current exact/property tests include:

- graph dense assembly, matvec, energy, duplicate and permutation invariance;
- disconnected components, singleton handling and RHS compatibility;
- exact SDDM augmentation, lifting and gauge-invariant extraction;
- heavy-edge tie behavior and a pinned-C-kernel forest split vector;
- deterministic forest component labels and low-effective-degree behavior;
- restriction/prolongation and dense-oracle Galerkin equality;
- every hierarchy terminal reason, strict level reduction, and repeat bounds;
- weighted-path direct factor values and fresh residuals;
- static-degree ordering on a star and disconnected direct solves;
- exact direct-terminal and upstream iterative-terminal preconditioner behavior;
- factor-based repeat calibration before a direct terminal;
- forced-multilevel linearity, numerical symmetry, positive action, and
  deterministic workspace reuse;
- exact one-iteration PCG with a direct preconditioner;
- zero-right-hand-side handling and quotient-space centering;
- forced-multilevel PCG with a fresh original-system certificate;
- disconnected PCG systems and explicit compatibility failures;
- batched/individual equality and reusable solver workspaces;
- explicit iteration-budget/nonconvergence errors.

Remaining tests cover the end-to-end SDDM wrapper, more graph families, extreme
weights, scale invariance, independent dense-oracle comparisons, and expanded
input-order determinism.

## 8. Intentional differences from the MATLAB interface

- Rust supports small matrices instead of refusing inputs below 500 vertices.
- Rust uses zero-based indices and typed errors instead of flags and warnings.
- Rust handles disconnected Laplacians component by component.
- Tie-breaking and aggregation order are explicitly deterministic.
- Every positive SDDM dominance excess is augmented exactly, including values
  below the MATLAB wrapper's numerical strict-dominance threshold.
- Rust exposes hierarchy diagnostics and certified solve results directly.
- A configurable maximum-level guard is added as a hard safety limit.
- The direct terminal grounds one vertex per component rather than assuming a
  connected graph with one final coordinate.
- PCG periodically replaces its recursive residual and always verifies
  convergence against a fresh original-system residual.

The hierarchy constants and stationary cycle remain faithful to the pinned
upstream implementation unless a deviation is recorded here.

## 9. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-22 | Pin upstream commit `19752fc...` | Stable target including corrected forest-size initialization |
| 2026-08-22 | GPL-3.0-only | Preserve source-derived upstream licensing |
| 2026-08-22 | No numerical runtime dependencies for v1 | Auditable and portable kernels |
| 2026-08-22 | Develop on `main` with checkpoint commits | Recoverability if the thread ends |
| 2026-08-22 | Deterministic, single-threaded first | Correctness before parallel optimization |
| 2026-08-22 | Augment every positive SDDM excess | Preserve the supplied matrix exactly |
| 2026-08-22 | Lowest-neighbor heavy-edge tie rule | Stable behavior matching sparse index order |
| 2026-08-22 | Ground the highest-index vertex per component | Deterministic disconnected extension of the upstream final-coordinate gauge |
| 2026-08-22 | Dense storage for the first terminal factor | Auditability first; terminal size is bounded by the direct threshold |
| 2026-08-22 | Reusable level workspaces via take/restore | Avoid application-time allocation without unsafe aliasing |
| 2026-08-22 | Fresh residual certification and periodic restarts | Prevent false convergence from recursively accumulated residual drift |

## 10. Current risks and open defects

- The formatted PCG checkpoint needs the full quality rerun triggered by this
  plan update because GitHub suppresses recursive workflow runs from
  formatting-bot commits.
- Dense LDL setup is cubic and should eventually be replaced or supplemented by
  sparse storage for unusually dense terminal levels; correctness comes first.
- The upstream hierarchy can stagnate on dense graphs. The Rust port preserves
  its iterative terminal fallback and reports it explicitly.
- The SDDM lift/extract primitives exist, but the end-to-end reusable SDDM solver
  and original-SDDM residual certificate are not yet implemented.
- Local Rust compilation is unavailable in the agent container, so every Rust
  checkpoint is validated through GitHub Actions.

## 11. Checkpoint log

| Checkpoint | Commit | CI | Notes |
|---|---|---|---|
| Phase 0 quality harness | `bb5d56e` | green | format, Clippy, docs, debug/release tests and builds on three OSes |
| Graph foundation | `cfd4d073` | green after rustfmt | deterministic graph, components, energy and dense oracle |
| Exact SDDM layer | `8c5f710b`–`9fa8af0b` | green | exact augmentation and focused tests on three OSes |
| Forest decomposition | `a5b80c9b`–`f5fc26d3` | green | heavy-edge, split kernel, low-degree correction, labels |
| Hierarchy construction | `0403d435`–`5b0a399b` | green | exact Galerkin contraction and all terminal guards |
| Terminal LDL^T and stationary cycle | `5abf4ed6`–`2184ff5d` | green | factorization, repeat calibration, recursion, workspaces |
| Certified PCG and batching | `62224219` | all tests green; initial format failure fixed | certificates, diagnostics, batches, explicit errors |
| PCG rustfmt | `9262e7a5` | full quality rerun triggered | formatted source checkpoint |

## 12. Current next action

Complete and qualify an end-to-end reusable SDDM solver that builds CMG on the
exact augmented Laplacian, lifts each right-hand side, extracts the original
solution, and independently certifies the residual in the original SDDM system.
Then expand adversarial and determinism tests across the required graph families
and weight regimes.
