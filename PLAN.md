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
- [ ] exact Galerkin coarse-graph contraction;
- [ ] upstream hierarchy terminal and stagnation rules;
- [ ] upstream nonzero-ratio recursive repeat schedule;
- [ ] grounded degree-ordered LDL^T terminal solver;
- [ ] stationary recursive CMG cycle with damped Jacobi smoothing;
- [ ] reusable immutable hierarchy and caller-owned workspaces;
- [ ] PCG with fresh original-system residual certification;
- [ ] repeated-right-hand-side API;
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
  coarsen.rs          Galerkin graph contraction
  ldl.rs              Grounded terminal LDL^T
  hierarchy.rs        CMG hierarchy construction and diagnostics
  workspace.rs        Reusable application workspaces
  preconditioner.rs   Stationary recursive CMG cycle
  pcg.rs              Certified PCG and batched solves
  diagnostics.rs      Build/apply/solve reports

tests/
  graph_exact.rs
  sddm_small.rs
  forest_exact.rs
  hierarchy_exact.rs
  preconditioner_properties.rs
  solve_small.rs
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
| 2. Forest decomposition | **implemented; final quality CI pending** | Golden parent/split/component tests green on three OSes |
| 3. Coarse graphs and hierarchy | **in progress locally** | Dense `R L R^T` and hierarchy-digest tests pass |
| 4. Terminal LDL^T | not started | Dense-reference solution and residual tests pass |
| 5. CMG cycle | not started | Linearity, symmetry, positivity, and recursion tests pass |
| 6. PCG and batching | not started | Certified end-to-end small solves pass |
| 7. Adversarial qualification | not started | Debug/release suites pass on all CI platforms |
| 8. Completion audit and docs | not started | Every upstream production routine is covered |

## 6. Implemented foundation

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
- upstream low-effective-degree correction and deterministic aggregate labels.

The exact augmentation intentionally improves on the MATLAB wrapper's numerical
`1e-13` classification: a small but positive row-sum excess is not discarded or
silently converted to a Laplacian.

## 7. Test matrix

Required graph families include paths, cycles, stars, trees, complete graphs,
grids, barbells, lollipops, bipartite worker-firm graphs, disjoint unions, and
isolated vertices. Tests construct compatible right-hand sides as `b = A x_star`
and verify freshly recomputed original-system residuals.

Current exact tests include:

- graph dense assembly, matvec, energy, duplicate and permutation invariance;
- disconnected components, singleton handling and RHS compatibility;
- exact SDDM augmentation, lifting and gauge-invariant extraction;
- heavy-edge tie behavior;
- a pinned-C-kernel long-path forest split vector;
- deterministic forest component labels;
- low-effective-degree behavior on an equal-weight clique;
- isolated-vertex forest grouping.

Remaining invariants include exact Galerkin contraction, preconditioner
linearity/symmetry/positivity, batch equality, and end-to-end residual
certification.

## 8. Intentional differences from the MATLAB interface

- Rust supports small matrices instead of refusing inputs below 500 vertices.
- Rust uses zero-based indices and typed errors instead of flags and warnings.
- Rust handles disconnected Laplacians component by component.
- Tie-breaking and aggregation order are explicitly deterministic.
- Every positive SDDM dominance excess is augmented exactly, including values
  below the MATLAB wrapper's numerical strict-dominance threshold.
- Rust exposes hierarchy diagnostics and certified PCG results directly.

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

## 10. Current risks and open defects

- The formatted forest checkpoint still needs a human-authored trigger commit
  because GitHub suppresses recursive workflow runs from its formatting bot.
- The upstream hierarchy can stagnate on dense graphs. The Rust port will
  preserve its iterative terminal fallback and report it explicitly.
- The terminal LDL^T implementation is intentionally simple and may be costly
  for a dense final level.
- Local Rust compilation is unavailable in the agent container, so every Rust
  checkpoint is validated through GitHub Actions.

## 11. Checkpoint log

| Checkpoint | Commit | CI | Notes |
|---|---|---|---|
| Phase 0 quality harness | `bb5d56e` | green | format, Clippy, docs, debug/release tests and builds on three OSes |
| Graph foundation | `cfd4d073` | green after rustfmt | deterministic graph, components, energy and dense oracle |
| Exact SDDM layer | `8c5f710b`–`9fa8af0b` | green | exact augmentation and focused tests on three OSes |
| Forest decomposition | `a5b80c9b` | tests green; quality rerun triggered | heavy-edge, split kernel, low-degree correction, labels |
| Forest rustfmt | `13a076ea` | pending | automatic formatting checkpoint |

## 12. Current next action

Inspect the formatted forest quality run and repair any Clippy/documentation
finding. Then implement deterministic Galerkin contraction and hierarchy level
construction with dense `R L R^T` equality tests.
