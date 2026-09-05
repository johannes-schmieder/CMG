# Changelog

All notable user-facing changes to CMG are documented here. This project uses
semantic-style version numbers; release dates use ISO `YYYY-MM-DD` format.

## Unreleased

### Added

- Opt-in experimental calibrated scalar/fused batch dispatch with private
  first-call trials, explicit overrides, soft time and principal-memory budgets,
  instance-local cached decisions, and numerical agreement checks. Existing
  solver defaults remain unchanged; performance promotion requires the bounded
  fresh-RHS SCC qualification.

- Explicit experimental four-lane independent-RHS Laplacian solves, with
  documented opt-in/memory constraints and separate profiling of active-lane
  occupancy and numerical phase costs. Existing solver routing is unchanged.
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

There has not yet been a final tagged release. The `0.1.0` value in
`Cargo.toml` is the version currently being prepared, not evidence of a
published release.
