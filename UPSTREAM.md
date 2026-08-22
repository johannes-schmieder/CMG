# Upstream provenance and coverage

## Pinned source

This project is a Rust port derived from the official CMG implementation:

- Ioannis Koutis and Gary Miller, `ikoutis/cmg-solver`
- pinned commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`
- source path `matlab/cmg`
- licensed under GNU GPL version 3

The upstream commit includes a 2026 correction in
`matlab/cmg/mex/forest_components.c`: the `sizes` workspace is initialized from
index zero rather than index one.

## Source-of-truth order

1. Pinned MATLAB implementation and C MEX kernels.
2. Published CMG algorithm and mathematical invariants.
3. Independent dense algebraic oracles used only for verification.

## Production-path coverage

| Upstream routine | Rust location | Status |
|---|---|---|
| `validate_input` | `src/sddm.rs` | planned |
| `steiner_group` | `src/forest.rs` | planned |
| `split_forest_` / `mx_splitforest_` | `src/forest.rs` | planned |
| `update_groups_` | `src/forest.rs` | planned |
| `forest_components_` / C kernel | `src/forest.rs` | planned |
| `Rt * A * Rt'` contraction | `src/coarsen.rs` | planned |
| hierarchy stopping logic | `src/hierarchy.rs` | planned |
| repeat-count logic | `src/hierarchy.rs` | planned |
| `ldl_` / `ldl_solve` | `src/ldl.rs` | planned |
| `preconditioner_` / C kernel | `src/preconditioner.rs` | planned |
| `preconditioner_sd` | `src/sddm.rs`, `src/preconditioner.rs` | planned |
| MATLAB `pcg` usage | `src/pcg.rs` | planned native implementation |
| sparse utility kernels | `src/graph.rs`, `src/workspace.rs` | planned |

## Behavioral constants in the pinned implementation

- direct terminal when the current graph has fewer than 700 vertices;
- damped Jacobi inverse diagonal `1 / (2 * diag(A))`;
- low-effective-degree threshold `1/8`;
- hierarchy cumulative-nonzero guard `5 * nnz(A_initial)`;
- stagnation when the coarse graph has at least `n - 1` vertices;
- repeat count `max(floor(nnz(A_fine) / nnz(A_coarse) - 1), 1)`;
- the public preconditioner applies one top-level stationary cycle; a level's repeat count controls the recursive solve at its child level;
- direct Laplacian solve grounds the final coordinate;
- strictly dominant SDDM matrices are augmented with one extra vertex.

The Rust implementation allows these values to be overridden for testing while
keeping the upstream values as defaults.

## Attribution

CMG was developed by Ioannis Koutis and Gary Miller. This repository is an
independent Rust port and is not represented as an official upstream release.
