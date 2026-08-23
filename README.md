# CMG in Rust

A deterministic Rust implementation of stationary Combinatorial Multigrid
(CMG) for weighted graph Laplacians and symmetric diagonally dominant
M-matrices (SDDM), with certified preconditioned conjugate-gradient solves.

The implementation follows the official `ikoutis/cmg-solver` source pinned at
commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`. Provenance and routine
coverage are recorded in [`UPSTREAM.md`](UPSTREAM.md); numerical qualification
is documented in [`PLAN.md`](PLAN.md), and the active optimization record is in
[`PERFORMANCE_PLAN.md`](PERFORMANCE_PLAN.md).

## Status

The complete stationary CMG path is implemented and tested on Linux, macOS,
and Windows. The suite covers exact small systems, disconnected graphs,
weighted adversarial cases, deterministic hierarchy construction, SDDM
augmentation, terminal factorization, repeated right-hand sides, and
original-system residual certification.

The serial crate has no runtime dependency. Optional multicore support uses a
package-owned Rayon pool behind the `parallel` Cargo feature. Performance work
is active; the repository does not yet claim controlled 8--32-core or NUMA
scaling because ordinary hosted CI exposes only four logical CPUs.

## Serial Laplacian solve

For the weighted path

```text
0 --1-- 1 --1-- 2
```

the Laplacian and a compatible right-hand side are

```text
L = [ 1 -1  0 ]       b = [ 1 ]
    [-1  2 -1 ]           [ 0 ]
    [ 0 -1  1 ]           [-1 ]
```

The zero-mean solution of `L x = b` is `[1, 0, -1]`.

```rust
use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};

fn main() -> Result<(), cmg::CmgError> {
    let graph = Laplacian::from_edges(
        3,
        [(0, 1, 1.0), (1, 2, 1.0)],
    )?;
    let rhs = [1.0, 0.0, -1.0];

    // Build once, then reuse for every RHS on the unchanged weighted graph.
    let cmg = CmgPreconditioner::build(&graph, CmgOptions::default())?;
    let result = solve_pcg(&graph, &cmg, &rhs, PcgOptions::default())?;

    println!("x = {:?}", result.solution());
    println!("iterations = {}", result.iterations());
    println!("backward error = {:.3e}", result.backward_error());
    Ok(())
}
```

Run the complete example with:

```text
cargo run --example laplacian_pcg
```

A graph Laplacian is singular. The submitted right-hand side must sum to zero
within every connected component, up to the configured compatibility tolerance.
Solutions are returned with a deterministic component-wise zero-mean
normalization.

## Opt-in parallel PCG

Enable multicore support in `Cargo.toml`:

```toml
cmg = { git = "https://github.com/johannes-schmieder/CMG", features = ["parallel"] }
```

Build a reusable executor and a selectively routed parallel plan:

```rust,ignore
use cmg::{
    CmgOptions, CmgPreconditioner, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions, PcgOptions, PcgWorkspace,
    solve_pcg_with_plan_and_workspace,
};

let cmg = CmgPreconditioner::build(&graph, CmgOptions::default())?;
let executor = ParallelExecutor::new(ParallelOptions {
    threads: 16,
    ..ParallelOptions::default()
})?;
let plan = ParallelCmgPlan::build(&cmg, &executor)?;
let mut workspace = PcgWorkspace::new(&cmg);

let result = solve_pcg_with_plan_and_workspace(
    &graph,
    &cmg,
    &plan,
    &rhs,
    PcgOptions::default(),
    &mut workspace,
    &executor,
)?;
```

`ParallelCmgPlan` stores row-oriented operators only for hierarchy levels where
the graph size and density clear conservative routing thresholds. Sparse
path-like hierarchies can therefore retain zero parallel operators and use the
compact serial edge kernels. `plan.operator_count()` and `plan.byte_len()` make
the routing and memory cost observable.

Run the worker--firm example with:

```text
cargo run --release --example parallel_pcg --features parallel
```

## Choosing a parallel strategy

- **One large or relatively dense RHS:** build one `ParallelCmgPlan` and use the
  planned PCG API.
- **Several independent RHSs:** `solve_pcg_batch_with_executor` runs independent
  certified serial solves concurrently and limits simultaneous workspaces with
  `workspace_memory_budget_bytes`.
- **Small or very sparse systems:** the serial solver is usually preferable.
- **Repeated solves:** reuse the preconditioner, plan, executor, and caller-owned
  workspace. Do not rebuild them for every RHS.

On the available four-logical-CPU hosted runner, the qualified planned-PCG
benchmark showed no change in iteration counts, residuals, backward errors, or
measured solutions. Directional full-solve speedups ranged from near parity on
a path graph to about 2.17x on the tested dense worker--firm graph. These are
benchmark records, not a general hardware guarantee; see
[`PERFORMANCE_PLAN.md`](PERFORMANCE_PLAN.md) for exact cases and gates.

## SDDM systems

`SddmMatrix` validates symmetric matrices with nonpositive off-diagonals and
diagonal dominance. `SddmSolver` performs the CMG Laplacian augmentation,
reuses the hierarchy for repeated right-hand sides, extracts the original SDDM
solution, and verifies the residual against the original system.

## Build and test

```text
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets
cargo test --all-targets --release
cargo test --all-targets --all-features
cargo test --all-targets --all-features --release
cargo build --release --all-features
```

## Scope boundary

The current crate implements the stationary CMG algorithm, certified PCG,
repeated-RHS operation, optional deterministic multicore kernels, and SDDM
wrapping. K-cycles, flexible CG, GPU kernels, NUMA-specific placement, the C
ABI, and Stata integration are separate future layers rather than hidden parts
of the current implementation.

## License

GNU GPL version 3 only. See [`LICENSE`](LICENSE) and [`UPSTREAM.md`](UPSTREAM.md).
