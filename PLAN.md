# CMG Rust Port — Live Implementation Plan

Last updated: 2026-08-22

## 1. Goal

Create a deterministic, production-oriented Rust port of the stationary
Combinatorial Multigrid (CMG) preconditioner published by Koutis, Miller, and
Tolliver and implemented in the official `ikoutis/cmg-solver` repository.
The first complete release includes the hierarchy builder, recursive
preconditioner, SDDM augmentation, a certified PCG driver, repeated-right-hand-
side support, diagnostics, and exact small-problem qualification.

## 2. Pinned upstream

The behavioral reference is:

- repository: `ikoutis/cmg-solver`
- commit: `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- commit date: 2026-07-05
- license: GNU GPL version 3

The pinned revision contains the upstream correction that initializes the first
forest-component size counter. See `UPSTREAM.md` for the source coverage table
and intentional Rust-interface deviations.

## 3. Definition of complete

The port is complete when all of the following are implemented and tested:

- [ ] strict input validation for weighted Laplacians and SDDM matrices;
- [ ] deterministic edge canonicalization and duplicate aggregation;
- [ ] connected components and Laplacian null-space compatibility checks;
- [ ] SDDM-to-Laplacian augmentation and solution extraction;
- [ ] maximum-weight incident-edge forest construction;
- [ ] faithful forest diameter/conductance splitting;
- [ ] low-effective-degree forest correction;
- [ ] deterministic forest-component labeling;
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
  options.rs          CMG and PCG options
  graph.rs            Canonical weighted undirected Laplacian
  sddm.rs             SDDM validation and augmentation
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
  forest_exact.rs
  hierarchy_exact.rs
  preconditioner_properties.rs
  solve_small.rs
  sddm_small.rs
  adversarial.rs
  determinism.rs
```

The numerical core initially has no runtime dependencies. Sparse graph kernels,
coarsening, terminal factorization, and PCG are implemented directly in safe
Rust. The initial implementation is single-threaded and deterministic.

## 5. Milestones

| Phase | Status | Gate |
|---|---|---|
| 0. Contract, provenance, CI | **in progress** | Plan, license, upstream pin, and quality CI committed |
| 1. Graph and SDDM core | not started | Dense assembly, energy, augmentation, and component tests pass |
| 2. Forest decomposition | not started | Golden parent/split/component tests pass |
| 3. Coarse graphs and hierarchy | not started | Dense `R L R^T` and hierarchy-digest tests pass |
| 4. Terminal LDL^T | not started | Dense-reference solution and residual tests pass |
| 5. CMG cycle | not started | Linearity, symmetry, positivity, and recursion tests pass |
| 6. PCG and batching | not started | Certified end-to-end small solves pass |
| 7. Adversarial qualification | not started | Debug/release suites pass on all CI platforms |
| 8. Completion audit and docs | not started | Every upstream production routine is covered |

## 6. Test matrix

Required graph families include paths, cycles, stars, trees, complete graphs,
grids, barbells, lollipops, bipartite worker-firm graphs, disjoint unions, and
isolated vertices. Tests use exact compatible right-hand sides `b = A x_star`
and verify freshly recomputed original-system residuals.

Required invariants include:

- `x^T L x = sum_e w_e (x_u - x_v)^2`;
- exact agreement between edge and dense assembly;
- exact Galerkin contraction;
- component-wise zero-sum residual compatibility;
- preconditioner linearity and numerical symmetry;
- positive quadratic form on the quotient space;
- deterministic output under edge permutation and duplicate-edge splitting;
- equality of individual and batched applications;
- no hidden ridge regularization or tolerance relaxation.

## 7. Intentional differences from the MATLAB interface

- Rust supports small matrices instead of refusing inputs below 500 vertices.
  The direct terminal threshold remains configurable and defaults to the
  upstream value.
- Rust uses zero-based indices and typed errors instead of MATLAB flags and
  console warnings.
- Rust exposes hierarchy diagnostics and certified PCG results directly.
- Rust handles disconnected Laplacians component by component rather than
  assuming a single connected graph.
- Tie-breaking and aggregation order are explicitly deterministic.

Algorithmic constants and stationary-cycle behavior remain faithful to the
pinned upstream implementation unless a deviation is recorded below.

## 8. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-22 | Pin upstream commit `19752fc...` | Stable source target including the corrected forest-size initialization |
| 2026-08-22 | GPL-3.0-only | Source-derived port preserves upstream licensing |
| 2026-08-22 | No numerical runtime dependencies for v1 | Auditable kernels and portable builds |
| 2026-08-22 | Develop on `main` with checkpoint commits | Recoverability if the development thread ends |
| 2026-08-22 | Deterministic, single-threaded first | Correctness and reproducibility before parallel optimization |

## 9. Current risks and open defects

- No known implementation defects yet; numerical code has not begun.
- The upstream hierarchy can stagnate on dense graphs. The Rust port will
  preserve the iterative terminal fallback and report it explicitly.
- The terminal LDL^T implementation is intentionally simple and may be costly
  for a dense final level; performance optimization is deferred until parity is
  established.
- Local Rust compilation is unavailable in the agent container, so every Rust
  checkpoint must be validated through GitHub Actions.

## 10. Checkpoint log

| Checkpoint | Commit | CI | Notes |
|---|---|---|---|
| Project approval | pending | pending | Implementation authorized; phase 0 started |

## 11. Current next action

Commit phase-0 documentation and quality CI, then implement the canonical graph,
components, and SDDM augmentation layer.
