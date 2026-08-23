# CMG performance and parallel execution guide

This guide describes how to use the optimized serial and optional parallel
execution paths. It complements `PERFORMANCE_PLAN.md`, which records the
benchmark evidence and retained/rejected experiments.

## Build modes

The default build has no parallel runtime dependency:

```bash
cargo build --release
```

Enable the package-owned Rayon pool and parallel APIs with:

```bash
cargo build --release --features parallel
```

The serial APIs remain available in both builds. Parallel support is opt-in and
does not change the stationary CMG algorithm, hierarchy repeat schedule, or
original-system residual certification.

## Automatic prepared solver

`ParallelPcgSolver` owns a parallel-built hierarchy, a selectively routed
`ParallelCmgPlan`, a package-owned thread pool, and reusable workspaces.

```rust
use cmg::{
    CmgOptions, Laplacian, ParallelOptions, ParallelPcgSolver, PcgOptions,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let graph = Laplacian::from_edges(
        4,
        [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0)],
    )?;

    let solver = ParallelPcgSolver::build(
        &graph,
        CmgOptions::default(),
        ParallelOptions {
            threads: 32,
            // Cap reusable simultaneous PCG workspaces, excluding the shared
            // immutable graph, hierarchy, and parallel plan.
            workspace_memory_budget_bytes: Some(64 * 1024 * 1024 * 1024),
            ..ParallelOptions::default()
        },
    )?;

    let rhs = [1.0, 0.0, 0.0, -1.0];
    let mut workspace = solver.workspace();
    let result = solver.solve_with_workspace(
        &rhs,
        PcgOptions::default(),
        &mut workspace,
    )?;

    println!("iterations = {}", result.iterations());
    println!("backward error = {:.3e}", result.backward_error());
    Ok(())
}
```

`threads: 0` requests the detected available parallelism. An explicit count is
recommended for reproducible deployment, shared servers, and Stata/plugin
integration.

## Automatic routing

The prepared solver chooses among three observable strategies:

1. **Serial** — compact one-edge-per-undirected-edge kernels and one workspace.
2. **Planned** — selected hierarchy levels use deterministic row-owned CSR
   kernels within one large solve.
3. **AcrossRightHandSides** — independent certified serial solves are assigned
   to the package-owned pool, sharing the immutable hierarchy but using private
   reusable workspaces.

The report can be inspected before or after solving:

```rust
let report = solver.select_batch_execution(right_hand_sides.len())?;
println!("strategy = {:?}", report.execution());
println!("concurrency = {}", report.concurrency());
println!("workspace pool bytes = {}", report.workspace_pool_bytes());
println!("parallel plan bytes = {}", report.plan_bytes());
```

The default one-RHS policy uses planned execution only when:

- more than one worker thread is available;
- the hierarchy contains at least one routed parallel operator; and
- the finest graph has at least 200,000 retained edges.

This is a performance heuristic, not a mathematical threshold. It can be
changed with `ParallelPcgPolicy` after benchmarking the target workload.

For multiple right-hand sides, across-RHS parallelism is preferred whenever the
configured memory budget permits at least two simultaneous workspaces. This is
currently the strongest and most reliable multicore path.

## Repeated right-hand sides

Reuse both the solver and its workspace pool:

```rust
let mut workspace = solver.workspace();
let batch = solver.solve_batch_with_workspace(
    &right_hand_sides,
    PcgOptions::default(),
    &mut workspace,
)?;

for result in batch.results() {
    assert!(result.backward_error().is_finite());
}
println!("execution = {:?}", batch.report().execution());
```

Do not rebuild the graph or CMG hierarchy for every variable. For fixed weights,
one hierarchy can serve the dependent variable, all regressors, and any other
inverse actions on the same two-way graph.

## Memory budgeting

Let:

- `H` be retained graph, hierarchy, terminal factors, and optional plan bytes;
- `W` be `solver.workspace_bytes()`;
- `B` be the configured workspace budget; and
- `q` be the number of simultaneous independent solves.

The scheduler limits concurrency approximately by

```text
q <= min(thread_count, rhs_count, floor(B / W)).
```

`workspace_memory_budget_bytes` governs the reusable workspace pool, not total
process memory. Leave room for `H`, right-hand sides, solutions, application
data, allocator overhead, and the host process.

For very large graphs, begin with a conservative budget and inspect the routing
report rather than assuming that one workspace per CPU is affordable.

## Recommendations by workload

### One small or medium right-hand side

Use the serial API. Thread startup, CSR plan memory, and reductions are unlikely
to pay for themselves.

### One very large right-hand side

Use `ParallelPcgSolver` with an explicit thread count. Automatic routing keeps
small/sparse hierarchy levels on compact serial kernels and parallelizes only
levels expected to amortize CSR and scheduling overhead.

### Many right-hand sides on one graph

Use the prepared batch API with a memory budget. Across-RHS parallelism usually
scales more predictably than parallelizing every sparse kernel, because each
solve is independent and synchronization is limited.

### Up to 32 or more CPUs

Set the intended thread count explicitly and provide a realistic workspace
budget. Machines with many cores should not automatically run one complete PCG
workspace per core. The memory-aware router may choose fewer simultaneous RHSs,
or planned within-solve execution when only one workspace is affordable.

Current hosted qualification covers one through four logical CPUs. The APIs and
memory controls support larger counts, but claims about 8-, 16-, 32-core, NUMA,
and very-high-memory scaling require controlled measurements on suitable
hardware.

## Tuning controls

`ParallelOptions` exposes:

- `threads`: package-owned pool size; zero means detected availability;
- `min_parallel_len`: lower work-size threshold for parallel kernels;
- `reduction_chunk_size`: fixed deterministic reduction chunk size;
- `workspace_memory_budget_bytes`: cap for simultaneous reusable PCG
  workspaces.

Avoid lowering thresholds simply to increase CPU utilization. The retained
benchmarks show that parallel execution can lose on path-like and modest sparse
levels while winning materially on sufficiently large or denser worker–firm
levels.

## Measurement discipline

Benchmark these quantities separately:

- graph canonicalization;
- hierarchy construction;
- one stationary CMG application;
- one certified PCG solve;
- repeated-RHS throughput;
- retained hierarchy and workspace bytes;
- process peak resident memory;
- iterations and final backward error.

A lower iteration count does not establish a faster implementation. Every
comparison should use the same graph, right-hand sides, compiler settings, and
residual certificate, and should alternate baseline and candidate runs on the
same machine.
