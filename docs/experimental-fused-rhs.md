# Experimental four-lane fused RHS

Keep the existing scalar/parallel solver routes as the default. The
`experimental-fused-rhs` feature provides an explicit alternative for repeated
zero-start Laplacian solves when a representative benchmark demonstrates a
worthwhile gain. Enabling the feature does not change automatic routing.

This API is experimental, not a stable release commitment. Pin a reviewed source
revision containing the feature; do not assume the current `main` installation
example includes experimental-branch work.

## What fusion does

Four independent PCG solves share graph and hierarchy traversals, with vectors
interleaved by vertex. Each RHS still has its own coefficients, iteration count,
convergence checks, residual replacement and final certificate. This is neither
block CG nor a multicore solver. Larger batches are processed serially in groups
of four; the last group may contain fewer RHS.

The implementation supports disconnected weighted Laplacians, contiguous and
strided batch views, empty batches and workspace reuse. It accepts zero initial
guesses only. It does not add an SDDM, warm-start, prepared changing-weight,
retained-preconditioner or parallel-solver routing interface.

Scalar per-RHS arithmetic order is retained. Tests compare output bits and
diagnostics against the scalar caller-buffer entrypoint, including partial
groups, disconnected graphs and mixed convergence. This evidence is not a
promise that floating-point results are identical across compilers or CPUs.

Batch layout, graph/preconditioner identity and workspace compatibility are
validated before solving. Per-RHS failures preserve the scalar-visible prefix:
successful preceding solutions and diagnostics are written in input order;
the failing RHS and later outputs remain untouched. Computation itself occurs
groupwise, so this does not promise that later RHS were never evaluated.

## Explicit use

Enable `experimental-fused-rhs` on the CMG dependency. The feature alone does not
require the parallel runtime. The following caller-buffer example is also
compiled and run by `tests/experimental_fused.rs`:

```rust
use cmg::experimental::{
    FusedPcgWorkspace4, solve_pcg_batch_fused_width4_into_with_workspace,
};
use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, PcgBatchMut, PcgBatchRef,
    PcgDiagnostics, PcgOptions,
};

fn main() -> Result<(), cmg::CmgError> {
    let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 1.0)])?;
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default())?;
    // Five RHS demonstrate a full group and an incomplete final group.
    let rhs = [1.0, 0.0, -1.0].repeat(5);
    let mut solutions = vec![0.0; rhs.len()];
    let mut diagnostics = vec![PcgDiagnostics::default(); 5];
    let mut workspace = FusedPcgWorkspace4::try_new(&preconditioner)?;
    solve_pcg_batch_fused_width4_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, 5, 3)?,
        PcgBatchMut::contiguous(&mut solutions, 5, 3)?,
        &mut diagnostics,
        PcgOptions::default(),
        &mut workspace,
    )?;
    // Retain the workspace for subsequent calls on the same hierarchy.
    assert!(workspace.byte_len() > 0);
    Ok(())
}
```

Use `try_new` for fallible workspace allocation. `byte_len` reports principal
retained workspace storage, not the graph, hierarchy, caller buffers, allocator
overhead or total process memory. The parallel solver's memory budget does not
govern this separately allocated workspace. Four packed lanes increase scratch
storage even for a short batch; do not allocate one per worker without budgeting
the combined memory. Benchmark scalar and fused calls with the same graph,
hierarchy, options, RHS and initial guesses, outside allocation/setup time.

## What the completed measurements support

The [experimental benchmark report](../benchmarks/report/fused-rhs-experiment.md)
contains the accepted Broadwell matrix and the limited Intel smoke observations.
On one-million-vertex Broadwell fixtures, the denser worker-firm cases achieved
1.69–2.20x speedups, but sparse mixed batches took 20–27% longer. Sparse
homogeneous gains at RHS 16/32 were only about 0.8%. The fused workspace was
3.4–3.7x the scalar batch workspace.

These are repeated-solve results, not end-to-end setup or parallel-throughput
comparisons. “Homogeneous” duplicates an identical RHS; each “mixed” group
contains a zero RHS, that homogeneous RHS and two other deterministic RHS.
Two graph families do not identify a density threshold. Seven paired timing
repetitions characterize within-allocation timing variation, not variability
across graphs, seeds, hosts or applications. The five other Intel smoke
observations contain only one timed pair each. No EPYC measurement is available.

Consequently, do not infer a density- or CPU-based dispatch policy from these
data. The new [calibrated dispatcher](calibrated-dispatch.md) instead measures
the caller's workload, owns selection/memory accounting, and exposes an explicit
downstream override. It remains opt-in and subject to separate qualification;
downstream code need not reproduce the fused solver or invent a density cutoff.

## Diagnosing mixed and partially occupied groups

Enable both `experimental-fused-rhs` and `profiling` and call
`profile_pcg_batch_fused_width4_into_with_workspace` with the same arguments as
the ordinary entrypoint. The profiling feature currently includes `parallel`,
but the fused call itself remains serial. It collects:

- `groups_by_rhs_count`: groups containing zero through four submitted RHS,
  including RHS that converge without iterating; index zero is unused.
- `iterations_by_active_lanes`: group iterations starting with one through four
  active RHS; converged lanes stop contributing. The lane-weighted sum equals
  the sum of successful per-RHS diagnostic iteration counts.
- Preconditioner, direction matvec and residual-recomputation call counts and
  nanoseconds by active lane count. Preconditioning includes recursive CMG;
  its internal matvecs are not counted again. Residual recomputation includes
  replacement/certification work, not just the fresh matvec.
- `other_solve_nanoseconds`: the remainder of solve time, including centering,
  reductions, vector updates, setup, control flow and instrumentation overhead.

Iteration-weighted occupancy is `active_lane_iterations /
lane_iteration_capacity`, where capacity is four times the group-iteration
count. It includes unfilled tail capacity, is undefined for zero-iteration
batches, and is not hardware SIMD utilization or a direct prediction of speedup.
Kernel time per call at each lane count helps separate poor occupancy from
expensive full-lane kernels, but remains a diagnostic comparison.

The ordinary solve uses a statically dispatched no-op observer: it does not read
profiling clocks or update counters. Obtain performance ratios from those
ordinary calls and collect a separate profile, since instrumentation perturbs
timing. The benchmark does this and checks output/diagnostic equality again.
Its additive `fused_detailed_profile` JSON object uses version
`cmg-fused-profile-v1` and includes per-RHS iteration/restart counts, occupancy
(`null` when undefined) and the phase histograms. Failed calls return the error,
not a partial profile.

For a bounded local diagnostic, without submitting cluster jobs:

```bash
cargo test --features experimental-fused-rhs --test experimental_fused
cargo test --all-features --test experimental_fused
cargo run --release --manifest-path benchmarks/Cargo.toml \
  --bin fused-rhs-experiment -- worker-firm 10000 5 mixed 1 3
```

Use the measurements first to locate full-lane versus low-occupancy costs.
An all-active fast path, scalar handling of sparse active groups, or regrouping
are hypotheses, not established optimizations. Any change must preserve per-RHS
arithmetic order, certification and observable error-prefix behavior. No such
optimization or automatic routing change is implemented by this profiling work.

The original campaign remains closed. A separately authorized bounded dispatch
study now freezes a 10% calibration gain margin before testing intermediate
degrees, distinct/heterogeneous fresh RHS, multiple sizes and allocations. Its
protocol and promotion gates are documented in the calibrated-dispatch guide;
old outputs are neither rerun nor overwritten.
