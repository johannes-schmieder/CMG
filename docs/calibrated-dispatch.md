# Experimental calibrated batch dispatch

`cmg::experimental::CalibratedPcgBatchSolver` owns selection between serial
scalar PCG and serial width-four fusion. Enable `experimental-fused-rhs`; no
Rayon dependency is required. This is opt-in: no existing scalar, SDDM,
prepared, warm-start, retained-preconditioner or parallel API changes route.
The new policy is implemented and locally tested; SCC performance qualification
is a separate gate, not an assumed result.

## Usage

The solver owns its immutable preconditioner and reusable workspaces. It accepts
zero-start, contiguous or strided Laplacian batches and caller-owned output and
diagnostic buffers. The example is exercised in `tests/calibrated_dispatch.rs`.

```rust
use cmg::experimental::{
    BatchDispatchMode, BatchDispatchOptions, CalibratedPcgBatchSolver,
};
use cmg::{CmgOptions, Laplacian, PcgBatchMut, PcgBatchRef,
          PcgDiagnostics, PcgOptions};

fn main() -> Result<(), cmg::CmgError> {
    let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 1.0)])?;
    let mut solver = CalibratedPcgBatchSolver::build(
        &graph, CmgOptions::default(), BatchDispatchOptions::default(),
    )?;
    let rhs = [1.0, 0.0, -1.0].repeat(5);
    let mut output = vec![0.0; rhs.len()];
    let mut diagnostics = vec![PcgDiagnostics::default(); 5];
    let report = solver.solve_batch_into(
        PcgBatchRef::contiguous(&rhs, 5, 3)?,
        PcgBatchMut::contiguous(&mut output, 5, 3)?,
        &mut diagnostics, PcgOptions::default(),
    )?;
    // The first answer is scalar; selected describes the next compatible call.
    println!("executed={:?}, selected={:?}", report.executed, report.selected);
    // Reuse with fresh RHS values from the same workload distribution.
    // On a distribution, CPU-affinity, machine or load-regime change:
    solver.reset_calibration();
    // Optional downstream override, with no trial solves:
    solver.set_mode(BatchDispatchMode::Scalar);
    Ok(())
}
```

`from_preconditioner` takes ownership of an already-built hierarchy. `Auto`,
`Scalar` and `Fused` are explicit modes. `set_mode` clears the decision;
`reset_calibration` also clears a calibration failure and releases the fused
candidate. Neither mutates the immutable graph or hierarchy.

## Frozen initial policy

1. Validate and execute the mandatory scalar solve normally. Its outputs and
   all diagnostic fields remain the first call's answer.
2. Keep scalar for fewer than four RHS, zero-iteration batches, inadequate
   memory, or insufficient time. The default **soft extra budget is 30 seconds**,
   starting after the scalar solve. An estimate of eleven times that solve can
   reject calibration before candidate allocation. A zero budget disables it.
3. Allocate private output scratch with the caller's output strides and private
   diagnostics; warm the fused candidate once. Then time **five paired trials**,
   alternating scalar-first and fused-first. Check output bits and every
   diagnostic field against the baseline outside every timed region.
4. Require complete, strictly positive timing pairs and a finite paired
   percentile-bootstrap interval containing the point ratio (10,000 resamples).
   Select fused only when the interval's upper endpoint is **below 0.90**, a
   confidence-supported 10% gain. Otherwise keep scalar. No density/CPU cutoff
   is inferred from the old measurements.
5. Release trial scratch. Release fused storage if scalar wins; if fused wins,
   retain both scalar and fused storage for reuse/reset. Report raw trials,
   selection reason, extra calibration time, peak bound, actual retained bytes,
   and estimated batches to amortize calibration.

No numerical solve is interrupted. The soft budget can overrun by one solve
plus its agreement check; it is not a hard real-time deadline. The initial
scalar solve, hierarchy construction and caller setup are outside that extra
budget. Break-even is an estimate from calibration median differences, not a
prediction of future traffic or a guarantee of end-to-end speedup.

The instance caches only the latest signature: RHS count, both input/output
strides and all PCG options. A signature change triggers fresh calibration.
Changed RHS *values* intentionally do not invalidate it. Callers must reset on
changed convergence distribution, graph (construct a new solver), CPU/affinity
or load regime. There is no process-global, disk or CPU-name cache. Cached calls
make no calibration allocations, trial solves, profiling updates or clock reads.

## Memory and errors

`workspace_memory_budget_bytes` limits principal scalar/fused workspaces and
private calibration scratch. It excludes immutable graph/hierarchy, caller
buffers, allocator overhead and incidental stack frames, consistent with CMG's
principal-storage convention. Bootstrap resampling arrays are included. The
checked candidate bound is deliberately conservative: worst-case component
storage and construction scratch may reject an otherwise fitting candidate.
This is not a process RSS cap. `retained_workspace_bytes` reports actual
principal retained storage; `calibration_peak_bound_bytes` is a preflight bound,
not a measured high-water mark. A rejected attempted bound can exceed the cap.

Failure to fit the mandatory scalar workspace is an error before solving.
Candidate allocation/time/memory limitations in Auto are reported scalar
fallbacks. Explicit Fused returns allocation/budget errors rather than changing
the requested route. Input or baseline solve errors retain ordinary scalar
prefix behavior. A candidate numerical error or bitwise/diagnostic mismatch
returns an error, leaves the completed scalar baseline intact, and requires
`reset_calibration` before another call. It is not silently downgraded to a
performance fallback.

## Qualification, not automatic promotion

The bounded [SCC dispatch study](../benchmarks/scc/README.md#calibrated-dispatch-qualification)
freezes this policy before measuring new results. It uses fresh same-class RHS
batches, three allocations per Intel profile, and shared-host one-core binding.
Results will not establish a universal density threshold, AMD performance,
parallel throughput, or isolation from other tenants. Both scalar and fused
selections must be exercised. Correctness, memory and overhead/non-regression
gates must pass before declaring this opt-in API qualified. Inconclusive or
failed holdouts block promotion; do not tune the margin on those holdouts and
then count them as independent validation.
