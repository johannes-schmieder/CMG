# CMG in Rust

A deterministic Rust port of the stationary Combinatorial Multigrid (CMG)
preconditioner for weighted graph Laplacians and symmetric diagonally dominant
M-matrices (SDDM).

The implementation target is the official `ikoutis/cmg-solver` repository at
commit `19752fc102f8cae8e34f66457bfaccb1aaa60375`. Development status and exact
qualification gates are tracked in [`PLAN.md`](PLAN.md); source provenance and
routine coverage are recorded in [`UPSTREAM.md`](UPSTREAM.md).

## Current status

The port is under active implementation. The repository is intentionally kept
buildable at every checkpoint, with Linux, macOS, and Windows CI.

## Intended API

```rust,ignore
use cmg::{CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, solve_pcg};

let graph = Laplacian::from_edges(
    3,
    [(0, 1, 1.0), (1, 2, 2.0), (0, 2, 0.5)],
)?;
let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default())?;
let result = solve_pcg(
    &graph,
    &preconditioner,
    &[1.0, -1.0, 0.0],
    PcgOptions::default(),
)?;
# Ok::<(), cmg::CmgError>(())
```

## License

GNU GPL version 3 only. See [`LICENSE`](LICENSE) and [`UPSTREAM.md`](UPSTREAM.md).
