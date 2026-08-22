from pathlib import Path

ROOT = Path('.')

README = r'''# CMG in Rust

A deterministic, safe-Rust port of the stationary **Combinatorial Multigrid
(CMG)** preconditioner for weighted graph Laplacians and symmetric diagonally
dominant M-matrices (SDDM).

The behavioral reference is the official `ikoutis/cmg-solver` implementation at
commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`. The numerical core implements
hierarchy construction, the stationary recursive cycle, exact SDDM
augmentation, component-aware terminal solves, certified PCG, reusable
workspaces, and repeated right-hand sides.

## Status

Version `0.1.0` is correctness-qualified on exact, exhaustive, randomized, and
adversarial small systems. Formatting, Clippy with warnings denied, rustdoc,
debug tests, release tests, and release builds run on Linux, macOS, and Windows.
The latest machine-readable result is stored in `.ci/latest.json`.

This qualification establishes implementation correctness on the tested
families. It is **not** a claim that the current single-threaded implementation
is already optimal for very large production graphs; large-scale performance,
parallelism, sparse terminal factorization, C ABI, and Stata integration are
separate follow-on phases.

## Features

- Canonical weighted undirected edge representation with deterministic
  duplicate aggregation.
- Exact sparse Laplacian matrix-vector products, energy evaluation, connected
  components, and quotient-space operations.
- Strict validation for Laplacians and SDDM matrices, including explicit
  rejection of non-finite derived arithmetic.
- Exact extra-vertex transformation for every positive SDDM
  diagonal-dominance excess.
- Deterministic maximum-weight incident-edge forest, upstream forest splitting,
  low-effective-degree correction, and Galerkin contraction.
- Upstream hierarchy stopping rules and recursive repeat calibration, including
  the grounded LDL lower-factor nonzero rule at a direct terminal.
- Stationary damped-Jacobi pre/post smoothing, recursive coarse correction, and
  deterministic prolongation.
- Component-grounded, degree-ordered LDLᵀ terminal solve.
- Certified PCG with periodic residual replacement and fresh residual
  verification against the original, unprojected right-hand side.
- Reusable immutable preconditioners and caller-owned workspaces for repeated
  right-hand sides.
- End-to-end reusable SDDM solver that certifies the extracted solution against
  the original SDDM matrix.
- Typed errors for invalid input, hierarchy defects, non-positive pivots, PCG
  breakdown, iteration exhaustion, failed residual verification, and non-finite
  numerical quantities.

## Laplacian example

```rust
use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};

# fn main() -> Result<(), cmg::CmgError> {
let graph = Laplacian::from_edges(
    4,
    [
        (0, 1, 1.0),
        (1, 2, 2.0),
        (2, 3, 1.5),
        (0, 3, 0.25),
    ],
)?;

let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default())?;
let rhs = [1.0, -2.0, 1.0, 0.0]; // sums to zero on the component
let result = solve_pcg(
    &graph,
    &preconditioner,
    &rhs,
    PcgOptions::default(),
)?;

assert!(result.residual_norm() <= result.tolerance());
println!("iterations: {}", result.iterations());
println!("backward error: {:.3e}", result.backward_error());
# Ok(())
# }
```

The returned Laplacian solution uses the deterministic zero-mean gauge within
each connected component.

## SDDM example

```rust
use cmg::{
    CmgOptions, PcgOptions, SddmMatrix, SddmSolver, ValidationOptions,
};

# fn main() -> Result<(), cmg::CmgError> {
let matrix = SddmMatrix::from_dense(
    &[
        vec![4.0, -1.0,  0.0],
        vec![-1.0, 3.0, -1.0],
        vec![0.0, -1.0,  2.0],
    ],
    ValidationOptions::default(),
)?;

let solver = SddmSolver::from_matrix(
    &matrix,
    CmgOptions::default(),
    ValidationOptions::default(),
)?;
let rhs = [9.0, -8.0, 7.0];
let result = solver.solve(&rhs, PcgOptions::default())?;

assert!(result.residual_norm() <= result.tolerance());
println!("solution: {:?}", result.solution());
# Ok(())
# }
```

For repeated right-hand sides, retain `CmgPreconditioner` or `SddmSolver` and a
compatible workspace rather than rebuilding the hierarchy.

## Public API map

| Task | Main API |
|---|---|
| Construct a Laplacian | `Laplacian::from_edges` |
| Inspect connected components | `Components::from_laplacian` |
| Build CMG | `CmgPreconditioner::build` |
| Allocate reusable CMG storage | `CmgPreconditioner::workspace` |
| Apply CMG | `apply`, `apply_into`, `apply_into_with_validation` |
| Solve one Laplacian RHS | `solve_pcg` |
| Reuse PCG storage | `solve_pcg_with_workspace` |
| Solve several Laplacian RHSs | `solve_pcg_batch` |
| Construct an SDDM matrix | `SddmMatrix::from_parts`, `from_dense` |
| Inspect exact augmentation | `SddmMatrix::augment` |
| Build reusable SDDM solver | `SddmSolver::from_matrix`, `build` |
| Solve SDDM systems | `solve`, `solve_with_workspace`, `solve_batch` |
| One-shot SDDM solve | `solve_sddm` |
| Inspect hierarchy | `CmgHierarchy`, `HierarchyBuildReport` |

## Numerical semantics

### Singular Laplacians

A Laplacian right-hand side must lie in the matrix range: its sum must be zero
on every connected component. Small cancellation defects within the configured
compatibility tolerance are projected exactly onto that range before the CMG
cycle. Final acceptance is still computed from a fresh matrix-vector product
against the original, unprojected right-hand side.

### Residual certificate

PCG reports a fresh Euclidean residual and a normwise backward-error quantity.
The acceptance threshold combines the configured absolute tolerance with a
relative scale based on

```text
||b||₂ + ||A||_bound ||x||₂.
```

Non-finite norms, bounds, products, tolerances, factors, or corrections produce
a typed error; they cannot be interpreted as convergence.

### Determinism

Endpoint order, duplicate-edge aggregation, tie-breaking, component labels,
aggregate labels, grounding, terminal ordering, hierarchy construction, and
batch results are deterministic. Tests explicitly permute and split input
edges and compare complete hierarchies and solutions.

## Qualification

See [`QUALIFICATION.md`](QUALIFICATION.md) for the complete test matrix and
claim boundary. Highlights include:

- all 1,099 labeled simple graphs with one through five vertices;
- 120 deterministic randomized weighted Laplacians;
- 120 deterministic randomized SDDM systems;
- paths, cycles, stars, trees, grids, cliques, barbells, lollipops, bipartite
  worker-firm graphs, disconnected unions, and isolated vertices;
- dense algebraic oracles, exact Galerkin identities, an independent allocating
  stationary-cycle oracle, and original-system residual verification;
- weights spanning several orders of magnitude, weak bridges, duplicates,
  permutations, and near-overflow/non-finite adversarial cases.

## Build and test

```text
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps
cargo test --all-targets
cargo test --all-targets --release
cargo build --release
```

The crate has no runtime dependencies and forbids unsafe code.

## Current limitations

- The implementation is single-threaded.
- The terminal factorization is intentionally simple and dense after grounding;
  unusually dense or large terminal levels may be expensive.
- The implementation is stationary CMG, not a K-cycle or flexible-CG variant.
- Large-scale setup time, memory, and throughput benchmarks are not part of the
  v0.1 correctness claim.
- There is not yet a C ABI, Stata plugin, or `reghdfe`-compatible command layer.
- PPML hierarchy-refresh and weight-update policies are outside this release.

## Provenance and license

CMG was developed by Ioannis Koutis and Gary Miller. This repository is an
independent Rust port and is not an official upstream release. Source mapping,
constants, and intentional differences are documented in
[`UPSTREAM.md`](UPSTREAM.md).

The project is licensed under GNU GPL version 3 only. See [`LICENSE`](LICENSE).
'''

UPSTREAM = r'''# Upstream provenance and implementation coverage

## Pinned source

This project is a Rust port derived from the official CMG implementation:

- repository: `ikoutis/cmg-solver`
- pinned commit: `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- principal source path: `matlab/cmg`
- upstream authors: Ioannis Koutis and Gary Miller
- upstream license: GNU GPL version 3

The pinned revision includes the July 5, 2026 correction in
`matlab/cmg/mex/forest_components.c` that initializes the first component-size
workspace entry.

## Source-of-truth order

1. The pinned MATLAB hierarchy implementation and C MEX kernels.
2. The published stationary CMG algorithm and its algebraic invariants.
3. Independent dense and allocating test oracles used for verification.

The Julia implementation is useful background but does not define behavioral
compatibility for this port.

## Production-path coverage

| Upstream production path | Rust implementation | Status and verification |
|---|---|---|
| input validation and SDDM classification | `src/sddm.rs`, `src/options.rs` | implemented; dense/sparse and adversarial tests |
| strictly dominant extra-vertex construction | `SddmMatrix::augment`, `SddmAugmentation` | implemented exactly for every positive excess |
| heaviest incident-edge selection (`steiner_group`) | `maximum_weight_forest` in `src/forest.rs` | implemented with deterministic index tie-breaking |
| forest diameter/conductance splitting (`split_forest_`, `mx_splitforest_`) | `split_forest` in `src/forest.rs` | ported and checked against pinned small vectors |
| low-effective-degree update (`update_groups_`) | forest grouping correction in `src/forest.rs` | implemented with upstream `1/8` default |
| forest connected components (`forest_components_`) | `forest_components` | implemented with corrected initialization semantics |
| restriction/prolongation and `R A Rᵀ` | `Aggregation` in `src/coarsen.rs` | exact edge contraction and dense Galerkin oracle |
| hierarchy terminals and stagnation logic | `CmgHierarchy::build` | all terminal reasons independently tested |
| nonzero-ratio recursive repeat | `repeat_from_nonzeros` | implemented and tested |
| direct-terminal repeat recalibration from `nnz(L)` | `GroundedLdl::factor_nonzeros`, `CmgPreconditioner::build` | implemented; public hierarchy state regression-tested |
| grounded terminal ordering and LDLᵀ (`ldl_`, `ldl_solve`) | `GroundedLdl` in `src/ldl.rs` | component-grounded deterministic extension; dense residual tests |
| recursive stationary preconditioner (`preconditioner_`, C kernel) | `CmgPreconditioner::apply_level` | independent allocating cycle oracle |
| strictly dominant extraction (`x[1:n] - x[n+1]`) | `SddmAugmentation::extract_solution` | implemented and gauge-invariance tested |
| MATLAB use of PCG | native `src/pcg.rs` | certified quotient-space PCG with fresh residuals |
| repeated inverse actions | immutable preconditioner/solver plus workspaces | individual/workspace/batch equality tests |
| sparse utility kernels | `src/graph.rs`, `src/workspace.rs` | direct safe-Rust implementations |

## Pinned behavioral constants

The Rust defaults preserve these upstream choices:

- direct terminal below 700 vertices;
- damped-Jacobi inverse diagonal `1 / (2 diag(A))`;
- low-effective-degree threshold `1/8`;
- hierarchy cumulative-nonzero guard `5 * nnz(A_initial)`;
- stagnation when the coarse graph has at least `n - 1` vertices;
- nonterminal repeat count
  `max(floor(nnz(A_fine) / nnz(A_coarse)) - 1, 1)`;
- the final recursive repeat before a direct terminal uses `nnz(L)` of the
  grounded unit-lower LDL factor;
- a direct connected Laplacian solve fixes one coordinate;
- a strictly dominant SDDM system is represented by one extra vertex.

Options expose selected constants so tests can force multilevel recursion on
small systems. Production defaults remain the pinned values.

## Deliberate Rust extensions

These are intentional reliability or interface extensions, not claims about the
upstream MATLAB API:

1. **All matrix sizes are accepted.** The MATLAB wrapper's under-500 refusal is
   not part of the numerical algorithm. Small Rust inputs use the configured
   terminal policy.
2. **Disconnected Laplacians are explicit.** Rust grounds one deterministic
   highest-index vertex in every component and returns a zero-mean solution per
   component.
3. **Every positive SDDM excess is preserved.** Rust does not discard a small
   positive dominance excess because it falls below a classification tolerance.
4. **Determinism is specified.** Endpoint order, duplicate aggregation,
   component labels, equal-weight tie-breaking, aggregates, and grounding have
   stable rules.
5. **Typed failures replace warnings and flags.** Invalid matrices, hierarchy
   inconsistencies, non-positive pivots, breakdowns, failed certificates, and
   non-finite derived values are errors.
6. **A maximum-level safety guard is available.** This prevents unbounded
   construction even if future changes defeat another stopping rule.
7. **PCG is native and certified.** It periodically replaces the recursive
   residual and immediately recomputes a fresh original-system residual at
   candidate convergence.
8. **Cancellation-scale range projection is explicit.** An accepted
   floating-point component-sum defect is projected to zero before CMG, while
   final acceptance remains against the submitted, unprojected right-hand side.
9. **The SDDM wrapper certifies the original matrix.** When the extracted SDDM
   residual requires a tighter augmented solve, Rust refines the augmented
   target without relaxing the user's original-system tolerance.
10. **Non-finite derived arithmetic is rejected early.** Overflow in degrees,
    row sums, matvecs, norms, tolerances, factors, smoothing, restriction, or
    prolongation cannot produce a false success.

## Fidelity checks

The test suite does not rely solely on end-to-end convergence. It separately
checks:

- pinned forest parent, split, and aggregate outcomes;
- exact dense `R A Rᵀ` equality;
- every hierarchy stopping mode;
- the direct-terminal `nnz(L)` repeat rule;
- terminal LDL residuals on connected and disconnected graphs;
- an independent allocating reproduction of the stationary recursive cycle;
- preconditioner linearity, symmetry, and positive action;
- dense-reference solutions and fresh original-system certificates;
- deterministic results after edge reordering, reversal, and duplicate
  splitting.

See `QUALIFICATION.md` for the broader exhaustive and adversarial matrix.

## Attribution

CMG was developed by Ioannis Koutis and Gary Miller. This repository is an
independent source-derived Rust port and is not represented as an official
upstream release.
'''

QUALIFICATION = r'''# Correctness qualification report

## Claim boundary

Version 0.1 is qualified as a deterministic stationary CMG implementation for
the exact, exhaustive, randomized, and adversarial **small systems** described
below. The qualification covers implementation algebra, numerical safety,
original-system residual certification, platform portability, and API
consistency.

It does not establish large-scale performance superiority, optimal memory use,
or production throughput for million-vertex graphs. Those require separate
benchmarks after parallel kernels and a sparse terminal factorization are
available.

## Acceptance gates

Every release checkpoint must pass:

```text
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps
cargo test --all-targets
cargo test --all-targets --release
cargo build --release
```

GitHub Actions runs the quality gate and debug/release tests on Ubuntu, macOS,
and Windows. `.ci/latest.json` records the tested commit and matrix outcome.

## Exact algebraic tests

The suite independently verifies:

- canonical endpoint order and deterministic duplicate summation;
- sparse matvec versus explicitly assembled dense matrices;
- `xᵀ L x = Σ_e w_e (x_u - x_v)²`;
- deterministic connected components including isolated vertices;
- range compatibility and exact component projection;
- exact SDDM extra-vertex augmentation and gauge-invariant extraction;
- restriction/prolongation dimensions and adjoint structure;
- exact dense Galerkin equality `L_c = R L Rᵀ`;
- grounded LDLᵀ factor residuals;
- every hierarchy terminal and safety guard;
- the upstream nonzero-ratio repeat rules;
- stationary-cycle equality with an independently allocating oracle;
- preconditioner linearity, numerical symmetry, and positive action;
- fresh original-system residual and backward-error diagnostics;
- individual, reusable-workspace, and batched result equality.

## Exhaustive labeled simple graphs

`tests/exhaustive_small.rs` enumerates **all 1,099 labeled simple graphs** with
one through five vertices:

| Vertices | Possible edges | Labeled graphs |
|---:|---:|---:|
| 1 | 0 | 1 |
| 2 | 1 | 2 |
| 3 | 3 | 8 |
| 4 | 6 | 64 |
| 5 | 10 | 1,024 |

For every graph, including every disconnected pattern and all isolated
vertices, the test:

1. constructs a deterministic target vector;
2. centers it separately in every connected component;
3. forms the compatible right-hand side `b = L x*`;
4. forces the small hierarchy threshold low enough to exercise multilevel
   construction when topology permits;
5. solves with certified PCG;
6. verifies the fresh residual certificate; and
7. compares the recovered zero-mean solution with `x*`.

## Deterministic randomized systems

The same test module uses a fixed, dependency-free pseudorandom generator for:

- **120 weighted Laplacians**, with varying density, disconnected cases,
  reversed and split duplicate edges, and weights spanning powers from `10^-4`
  through `10^4`;
- **120 SDDM systems**, dimensions one through ten, random sparse M-matrix
  structure, heterogeneous weights, and positive dominance excesses.

Every right-hand side is generated from a known target. The resulting solution
is certified against the original matrix and compared with that target.

## Named graph families and numerical stresses

Additional tests cover:

- paths and cycles;
- stars and trees;
- two- and three-dimensional-style grids;
- complete graphs and dense-stagnation terminals;
- barbells and weak bridges;
- lollipops;
- bipartite worker-firm-style graphs;
- disjoint unions and isolated vertices;
- equal-weight ties and extreme degree imbalance;
- duplicate edges and input permutations;
- weights across many orders of magnitude;
- singular Laplacian SDDM blocks and strictly dominant SDDM systems;
- zero right-hand sides and deliberately incompatible right-hand sides;
- exhausted iteration budgets and typed PCG breakdowns.

## Non-finite and overflow safety

`tests/nonfinite.rs` deliberately probes cases that can overflow despite finite
inputs. The implementation rejects, rather than propagates, non-finite values
arising in:

- weighted degree and SDDM row-sum accumulation;
- graph and SDDM matrix-vector products;
- graph energies;
- component sums, tolerances, and projections;
- restriction and prolongation;
- grounded LDLᵀ factorization and substitution;
- iterative-terminal and recursive Jacobi smoothing;
- operator-norm bounds, solution norms, residual norms, tolerance scales, and
  backward-error denominators;
- SDDM right-hand-side lifting and extracted solutions.

A non-finite diagnostic can never satisfy the convergence test.

## Determinism qualification

Tests rebuild equivalent systems after:

- permuting edge order;
- reversing endpoints;
- replacing one edge by several duplicates whose weights sum to the original;
- changing right-hand-side batching boundaries.

They compare canonical graphs, hierarchy structure, repeat counts,
preconditioner output, iteration diagnostics, and final solutions.

## Platform qualification

The maintained CI matrix is:

- `ubuntu-latest`;
- `macos-latest`;
- `windows-latest`.

Both debug and release tests run on every platform. Ubuntu additionally enforces
formatting, Clippy with warnings denied, and warning-free rustdoc.

## Deferred qualification

The following are intentionally outside the v0.1 claim:

- million-vertex setup and solve throughput;
- memory scaling under very large or unusually dense coarse graphs;
- multi-core and SIMD speedup;
- sparse terminal factorization performance;
- C ABI and Stata plugin loading;
- comparison with `reghdfe`, LSMR, or alternative preconditioners on empirical
  worker-firm graphs;
- PPML hierarchy reuse under changing IRLS weights.
'''

PLAN = r'''# CMG Rust Port — Live Implementation Plan

Last updated: 2026-08-22

## 1. Goal

Create a deterministic, production-oriented Rust port of the stationary
Combinatorial Multigrid preconditioner developed by Koutis, Miller, and
Tolliver, pinned to the official `ikoutis/cmg-solver` source at commit
`19752fc102f8cae8e34f66457bfaccb1aaa60375`.

The v0.1 deliverable includes hierarchy construction, stationary recursive
application, exact SDDM augmentation, component-aware direct terminals,
certified PCG, reusable workspaces, repeated right-hand sides, diagnostics, and
small-system correctness qualification.

## 2. Completion checklist

- [x] pinned source, provenance, and GPL-3.0-only licensing;
- [x] deterministic weighted graph representation and duplicate aggregation;
- [x] connected components and quotient-space operations;
- [x] strict Laplacian and SDDM validation;
- [x] exact SDDM extra-vertex augmentation and extraction;
- [x] maximum-weight incident-edge forest;
- [x] pinned forest diameter/conductance splitting;
- [x] low-effective-degree correction;
- [x] deterministic forest components and aggregate labels;
- [x] exact Galerkin coarse graph;
- [x] upstream hierarchy terminals, fill guard, stagnation guard, and repeats;
- [x] direct-terminal repeat calibration from grounded `nnz(L)`;
- [x] component-grounded degree-ordered LDLᵀ terminal solver;
- [x] stationary damped-Jacobi recursive CMG cycle;
- [x] immutable preconditioner and caller-owned workspace;
- [x] native PCG with fresh original-system residual certification;
- [x] repeated-right-hand-side Laplacian API;
- [x] reusable end-to-end SDDM solver and batching;
- [x] independent dense and stationary-cycle oracles;
- [x] exhaustive enumeration of all labeled simple graphs through five vertices;
- [x] deterministic randomized Laplacian and SDDM qualification;
- [x] non-finite-derived-value and overflow hardening;
- [x] Linux, macOS, and Windows debug/release CI;
- [x] executable examples, qualification report, and final source-coverage audit.

## 3. Architecture

```text
src/
  lib.rs              Public exports
  error.rs            Typed validation/build/apply/solve errors
  options.rs          Validation, CMG, and PCG options
  graph.rs            Canonical weighted undirected Laplacian
  components.rs       Connected components and quotient-space operations
  sddm.rs             SDDM validation and exact augmentation
  forest.rs           Heavy-edge forest, splitting, and components
  coarsen.rs          Restriction, prolongation, and Galerkin contraction
  hierarchy.rs        Hierarchy construction, terminals, repeats, diagnostics
  ldl.rs              Component-grounded terminal LDLᵀ
  workspace.rs        Reusable application workspaces
  preconditioner.rs   Stationary recursive CMG cycle
  pcg.rs              Certified PCG and batched solves
  sddm_solver.rs      Reusable original-matrix-certified SDDM solver
```

The numerical core is safe, single-threaded Rust with no runtime dependencies.

## 4. Milestone status

| Phase | Status | Closed gate |
|---|---|---|
| 0. Contract, provenance, and CI | **complete** | pinned source, license, live plan, three-platform harness |
| 1. Graph and SDDM core | **complete** | dense assembly, energy, components, exact augmentation |
| 2. Forest decomposition | **complete** | heavy-edge, split, low-degree, and component tests |
| 3. Coarse graphs and hierarchy | **complete** | exact `R L Rᵀ`, all terminals, repeat schedule |
| 4. Terminal LDLᵀ | **complete** | connected/disconnected factor and residual tests |
| 5. Stationary CMG cycle | **complete** | linearity, symmetry, positivity, independent cycle oracle |
| 6. PCG, batching, and SDDM wrapper | **complete** | fresh certificates, workspaces, batches, refinement |
| 7. Adversarial qualification | **complete** | exhaustive, randomized, extreme-scale, non-finite tests |
| 8. Completion audit and documentation | **complete** | routine mapping, full license, examples, claim boundary |

## 5. Qualification summary

The maintained suite includes:

- all 1,099 labeled simple graphs through five vertices;
- 120 deterministic random weighted Laplacians;
- 120 deterministic random SDDM systems;
- named sparse, dense, bottleneck, disconnected, isolated, and bipartite graph
  families;
- dense algebraic and independently allocating recursive-cycle oracles;
- hierarchy determinism under edge permutation, reversal, and duplicate
  splitting;
- component-compatible and deliberately incompatible right-hand sides;
- debug and release execution on Linux, macOS, and Windows;
- overflow and non-finite probes for every major numerical layer.

See `QUALIFICATION.md` for the exact claim boundary.

## 6. Deliberate extensions beyond the MATLAB interface

- support for all matrix sizes rather than refusing small inputs;
- deterministic behavior specified at every ordering and tie boundary;
- explicit disconnected-component handling and one anchor per component;
- exact preservation of every positive SDDM dominance excess;
- typed errors instead of warnings, status flags, or silent regularization;
- maximum hierarchy-level safety guard;
- native certified PCG and repeated-right-hand-side workspaces;
- cancellation-scale range projection with final original-RHS verification;
- early rejection of non-finite derived arithmetic;
- original-matrix certification and adaptive augmented refinement for SDDM.

## 7. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-22 | Pin upstream `19752fc...` | stable source including corrected forest-component initialization |
| 2026-08-22 | GPL-3.0-only | preserve source-derived upstream licensing |
| 2026-08-22 | No runtime numerical dependencies | portable and auditable reference kernels |
| 2026-08-22 | Direct checkpoint commits on `main` | recoverability if the implementation thread ends |
| 2026-08-22 | Deterministic single-threaded reference first | correctness before optimization |
| 2026-08-22 | Exact positive-excess SDDM augmentation | preserve the supplied matrix |
| 2026-08-22 | Highest-index anchor per component | deterministic disconnected extension |
| 2026-08-22 | Dense grounded terminal in v0.1 | simple auditable correctness baseline |
| 2026-08-22 | Fresh residual certification | prevent false convergence from recursive residual drift |
| 2026-08-22 | Original-RHS acceptance after projection | tolerate cancellation without weakening the requested solve |
| 2026-08-22 | Explicit non-finite-derived-value errors | overflow cannot become a false success |

## 8. Current limitations and follow-on work

The complete v0.1 reference implementation deliberately leaves these future
phases open:

1. sparse terminal factorization and large-graph memory optimization;
2. multi-threaded and SIMD graph kernels;
3. large empirical benchmark suite and automatic backend router;
4. C ABI and cross-platform Stata plugin packaging;
5. two-way HDFE residualization and regression layer;
6. PPML hierarchy-refresh policies under changing IRLS weights;
7. optional K-cycle or flexible-CG variants.

These are performance and integration extensions. They are not missing pieces
of the stationary CMG port defined for v0.1.

## 9. Recovery state

All implementation, tests, documentation, and CI configuration are committed to
`main`. The latest tested commit and cross-platform matrix are recorded in
`.ci/latest.json`; no unpushed implementation state is required to resume work.
'''

EXAMPLE_LAPLACIAN = r'''use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let graph = Laplacian::from_edges(
        4,
        [
            (0, 1, 1.0),
            (1, 2, 2.0),
            (2, 3, 1.5),
            (0, 3, 0.25),
        ],
    )?;
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default())?;
    let rhs = [1.0, -2.0, 1.0, 0.0];
    let result = solve_pcg(&graph, &preconditioner, &rhs, PcgOptions::default())?;
    println!("solution = {:?}", result.solution());
    println!("iterations = {}", result.iterations());
    println!("backward error = {:.3e}", result.backward_error());
    Ok(())
}
'''

EXAMPLE_SDDM = r'''use cmg::{CmgOptions, PcgOptions, SddmMatrix, SddmSolver, ValidationOptions};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let matrix = SddmMatrix::from_dense(
        &[
            vec![4.0, -1.0, 0.0],
            vec![-1.0, 3.0, -1.0],
            vec![0.0, -1.0, 2.0],
        ],
        ValidationOptions::default(),
    )?;
    let solver = SddmSolver::from_matrix(
        &matrix,
        CmgOptions::default(),
        ValidationOptions::default(),
    )?;
    let rhs = [9.0, -8.0, 7.0];
    let result = solver.solve(&rhs, PcgOptions::default())?;
    println!("solution = {:?}", result.solution());
    println!("original-system residual = {:.3e}", result.residual_norm());
    Ok(())
}
'''

(ROOT / 'README.md').write_text(README)
(ROOT / 'UPSTREAM.md').write_text(UPSTREAM)
(ROOT / 'QUALIFICATION.md').write_text(QUALIFICATION)
(ROOT / 'PLAN.md').write_text(PLAN)
(ROOT / 'examples').mkdir(exist_ok=True)
(ROOT / 'examples/laplacian.rs').write_text(EXAMPLE_LAPLACIAN)
(ROOT / 'examples/sddm.rs').write_text(EXAMPLE_SDDM)
