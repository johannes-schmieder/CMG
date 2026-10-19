# Changelog

All notable user-facing changes to CMG are documented here. This project uses
semantic-style version numbers; release dates use ISO `YYYY-MM-DD` format.

## Unreleased

### Added

- Allocation-free fixed-cycle interleaved multi-RHS applications through
  `CmgBatchWorkspace` and `apply_compatible_batch_into`, with checked workspace
  admission, capacity ownership accounting, and optional existing parallel plans.
  Capacity one retains the scalar path; wider tiles share matrix, transfer and
  factor traversals and use serial compensated coarse sums, whose rounding may
  differ from scalar parallel chunk reductions. Existing APIs are unchanged.
- Deterministic stationary CMG preconditioning and certified PCG solves for
  weighted graph Laplacians and SDDM systems.
- Optional package-owned parallel execution and reusable repeated-right-hand-side
  solvers.
- Checked conservative pre-build memory estimates and exact retained-memory
  reports for prepared parallel solvers and workspace pools.
- Cross-platform correctness tests, reproducible benchmark harnesses, and pinned
  upstream provenance.
- Prepared fixed-topology Laplacians for deterministic changing-weight numeric
  assembly, solution-free caller-buffer PCG diagnostics, checked strided batch
  views, optional warm starts, and explicit certified retained-preconditioner
  solves.
- Conservative and exact repeated-solve memory accounting that separates
  prepared topology, current numeric state, stale hierarchy/plan, assembly
  scratch, workspace pools, and caller buffers.
- Opt-in `experimental-components` builders for compact coarse graphs and
  ordered sparse terminal factors, with an option to preserve the original
  hierarchy stopping rules; component benchmark fixtures and allocation checks.

### Changed

- Planned within-solve PCG uses fixed-order parallel component centering for
  large connected systems, preserving deterministic results across eligible
  multithreaded worker-pool sizes while one worker retains the serial path;
  prepared automatic routing can select these qualified vector kernels even
  when no hierarchy operator qualifies for row-parallel storage.
- Dense parallel-plan construction fills deterministic row-owned CSR blocks in
  parallel, and prepared solvers compute workspace requirements without a
  throwaway allocation.
- Reused CMG/PCG workspaces skip redundant vector clears and repeated internal
  validation after the public solve boundary has validated the complete
  workspace once.
- Controlled serial and hosted construction is cancellation-only. The
  experimental controlled builders (`Laplacian::from_edges_controlled`,
  `Laplacian::from_edges_hosted_controlled` and
  `CmgPreconditioner::build_component_experiment_serial_controlled`) take a
  sticky `Cancellation` query instead of a work budget and control callback.
  They poll it at a fixed cadence of counted primitive operations and before
  every allocation, and report `CmgError::Cancelled { phase }`.
  `ControlledStage::admit` validates chunk geometry only. Hierarchies, factors
  and cycle bits of successful builds are unchanged.

### Removed

- The construction-work accounting interface: `BuildControl`, `BuildBoundary`,
  `BuildOperation`, `BuildDecision`, `BuildStopReason`, `CmgError::BuildStopped`,
  the `BuildPrefix` grant ledger and its prefix types,
  `build_component_experiment_serial_controlled_with_factor_preflight`,
  `workspace_allocation_bytes_controlled`, `try_workspace_controlled`,
  `scalar_cycle_work_bound_controlled` and `AdmittedStage::charged_work`.
  Use `workspace_allocation_bytes` with `try_workspace` for admitted scalar
  workspaces.

There has not yet been a final tagged release. The `0.1.0` value in
`Cargo.toml` is the version currently being prepared, not evidence of a
published release.
