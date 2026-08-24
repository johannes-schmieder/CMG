# Development guide

## Repository policy

The current tree is the maintained product; Git history and Actions artifacts are the experiment archive. Do not leave completed one-shot optimization scripts, self-removing workflows, recovery launchers, or per-experiment JSON records on `main`.

Keep durable files only when they serve production code, correctness/regression tests, runnable examples, reusable benchmark/profiling harnesses, durable CI/benchmark workflows, maintained documentation, or a small number of current machine-readable status records.

## Module map

| Module | Responsibility |
|---|---|
| `graph.rs` | canonical weighted Laplacian storage, matvec, graph metadata |
| `components.rs` | connected components and quotient-space operations |
| `forest.rs` | heavy-edge forest, splitting, correction, component labels |
| `coarsen.rs` | aggregate mappings and Galerkin contraction |
| `hierarchy.rs` | recursive hierarchy construction and stopping rules |
| `ldl.rs` | grounded terminal factorization and solve |
| `preconditioner.rs` | stationary recursive CMG cycle |
| `pcg.rs` | certified PCG and repeated-RHS interfaces |
| `sddm.rs` / `sddm_solver.rs` | SDDM validation, augmentation, solve wrapper |
| `workspace.rs` | reusable numerical scratch storage |
| `execution.rs` | optional package-owned parallel executor |
| `csr.rs` | deterministic row-oriented operators for parallel kernels |
| `parallel_solver.rs` | automatic memory-aware parallel routing |
| `pcg_profile.rs` | profiling-only PCG instrumentation |

The public API is re-exported from `src/lib.rs`; implementation details should remain private unless a stable user-facing need exists.

## Correctness invariants

Performance work must preserve deterministic canonical graph construction, exact Laplacian symmetry, component-wise compatibility checks, pinned stationary CMG hierarchy/repeat semantics, deterministic grounding/normalization, final residual/backward-error verification against the original system, and hierarchy metadata unless the algorithm is deliberately changed and separately reviewed.

Never accept a benchmark improvement by relaxing tolerances, changing the RHS or matrix, skipping certification, or silently changing the stationary algorithm.

## Build and test

```bash
cargo fmt --all -- --check
cargo fmt --manifest-path benchmarks/Cargo.toml --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo clippy --manifest-path benchmarks/Cargo.toml --all-targets -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --document-private-items --all-features
cargo test --all-targets
cargo test --all-targets --release
cargo test --all-targets --all-features
cargo test --all-targets --all-features --release
cargo build --release --all-features
cargo build --release --manifest-path benchmarks/Cargo.toml --all-targets
```

`rust.yml` runs the cross-platform qualification matrix on Ubuntu, macOS, and Windows. The latest published status is `.ci/latest.json`.

## Performance work

Use the durable binaries in `benchmarks/` rather than committing a long-lived executable for every hypothesis. Temporary one-shot gates should be deleted once a decision is recorded; completed experiment artifacts should not remain on `main`.

Durable benchmark workflows:

- `performance.yml` — frozen-baseline serial performance plus certified routing matrix;
- `parallel-performance.yml` — hosted-runner parallel scaling;
- `c-kernel.yml` — pinned C/Rust differential comparison;
- `manual-32-thread-qualification.yml` — controlled large-machine scaling.

See `docs/PERFORMANCE.md` for interpretation.

## Documentation maintenance

Keep the root directory small. User-facing orientation belongs in `README.md`; maintained technical notes belong in `docs/`. Avoid chronological status logs in the current tree: commit history already records chronology.

When a user-visible implementation constant or routing threshold changes, update its code comment/tests and the maintained documentation in the same checkpoint.

## Scope boundary

The crate targets stationary CMG and certified PCG. C ABI/Stata integration, flexible Krylov methods, GPU support, and NUMA-specific tuning are explicit later layers rather than cleanup or micro-optimization work.
