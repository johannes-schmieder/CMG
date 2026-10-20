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
  experimental controlled builders (since consolidated into `BuildContext`'s
  `cancel` query, below) take a sticky `Cancellation` query instead of a work
  budget and control callback.
  They poll it at a fixed cadence of counted primitive operations and before
  every allocation, and report `CmgError::Cancelled { phase }`.
  `ControlledStage::admit` validates chunk geometry only. Hierarchies, factors
  and cycle bits of successful builds are unchanged.
- `CmgPreconditioner::workspace_allocation_bytes_controlled` and
  `try_workspace_controlled` are now cancellation-only. They share one
  implementation with `workspace_allocation_bytes` and `try_workspace`, return
  the same bytes, layout, contents and errors, poll the `Cancellation` before
  every allocation, at the construction cadence while initializing storage and
  before returning, and report `CmgError::Cancelled { phase }`.

- One construction pipeline. `CmgPreconditioner::build_with(graph,
  BuildOptions, BuildContext)`, `CmgHierarchy::build_with`,
  `Laplacian::build`/`from_edges_with` and `GroundedLdl::factor_with` replace
  the serial, budgeted, executor, controlled and component-experiment builder
  cross-product. `BuildOptions` carries the hierarchy and component options;
  `BuildContext` carries an optional borrowed `Team`, an optional sticky
  `Cancellation` and an optional CMG-owned requested-layout budget. Hierarchies,
  terminal factors, repeat counts and cycle bits do not depend on the context.
  `CmgPreconditioner::construction_allocation_bound(n, m, options, parallel)`
  merges the serial, executor and component bounds with identical values.
- One cycle algorithm. Scalar, planned and interleaved applications share one
  level recursion over the two existing sparse operator kernels (canonical edge
  list or row-owned CSR). `ApplyContext { plan, team, cancel }` replaces the
  separate plan/executor entry points; `apply_projected_into` and
  `apply_compatible_into_with` are the validating and prevalidated scalar
  entries, and `apply_compatible_batch_into` takes the same context.
- One PCG recurrence. `SolveContext { plan, team, concurrent_right_hand_sides,
  retained_preconditioner, cancel }` selects serial, planned, across-RHS and
  retained-preconditioner solves through `solve_pcg_with`, `solve_pcg_into` and
  `solve_pcg_batch_into`; `solve_pcg_batch_concurrent` keeps the owned-vector
  across-RHS convenience and `profile_pcg`/`profile_pcg_batch_into` record the
  same recurrence. Reduction orders are unchanged for every context.
- Host-owned execution. `Team::new(&pool, worker_ceiling, ParallelOptions)`
  borrows a caller's Rayon pool; `ParallelExecutor::team()` lends an owned
  pool for standalone use. `ParallelPcgSolver`, `ParallelCmgPlan`,
  `ControlledStage`/`AdmittedStage::run` and every parallel kernel take a
  `Team` and never create a pool; `ParallelPcgSolver` records the team's shape
  and rejects a differently shaped team.
- Construction, application and PCG are cancellable with a `Cancellation` in
  their context: counted operations are polled at a fixed cadence, parallel
  lanes poll between bounded sub-ranges, all lanes join before
  `CmgError::Cancelled` is returned, and partial outputs are discarded.
  `CmgBatchWorkspace::try_new_controlled` adds cancellable batch-workspace
  preparation with the layout and contents of `try_new`.
- Profiling builds (`build_with_profiled`, `ParallelCmgPlan::build_profiled`,
  `profile_apply_compatible_into`, `profile_pcg*`) record the production
  kernels instead of maintaining separate copies.

### Removed

- The construction-work accounting interface: `BuildControl`, `BuildBoundary`,
  `BuildOperation`, `BuildDecision`, `BuildStopReason`, `CmgError::BuildStopped`,
  the `BuildPrefix` grant ledger and its prefix types,
  `build_component_experiment_serial_controlled_with_factor_preflight`, the
  work-metered forms of `workspace_allocation_bytes_controlled` and
  `try_workspace_controlled`, `scalar_cycle_work_bound_controlled` and
  `AdmittedStage::charged_work`.
- The superseded entry points of the consolidated pipelines:
  `Laplacian::from_edges_with_executor`, `from_edges_controlled` and
  `from_edges_hosted_controlled`; `CmgHierarchy::build_serial_with_budget`,
  `build_with_executor`, `build_with_executor_and_cmg_budget`,
  `serial_construction_allocation_bound` and
  `executor_construction_cmg_allocation_bound`; `CmgPreconditioner::
  build_serial_with_budget`, `build_with_executor`, `build_with_executor_profiled`,
  `build_with_executor_and_cmg_budget`, `build_component_experiment`,
  `build_component_experiment_serial_with_budget`,
  `build_component_experiment_serial_controlled`,
  `build_component_experiment_with_executor`,
  `build_component_experiment_with_executor_and_cmg_budget` and their
  `*_allocation_bound` companions; `GroundedLdl::factor_with_budget` and
  `factor_by_component_with_budget`; `Aggregation::contract_with_executor`;
  `apply_into_with_validation`, `apply_compatible_into_with_validation` and the
  `ParallelCmgPlan` apply methods; the 17 `solve_pcg_*_with_*` variants and
  `profile_pcg_with_plan`; `HostExecutor`, `InstalledTeam` and
  `ParallelExecutor::with_installed`; `maximum_weight_forest_with_executor` and
  `build_forest_grouping_with_executor`; `ParallelPcgSolver::executor` and
  `plan`.

There has not yet been a final tagged release. The `0.1.0` value in
`Cargo.toml` is the version currently being prepared, not evidence of a
published release.
