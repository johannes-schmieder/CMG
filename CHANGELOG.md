# Changelog

All notable user-facing changes to CMG are documented here. This project uses
semantic-style version numbers; release dates use ISO `YYYY-MM-DD` format.

## Unreleased

### Added

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

- Default-study candidate: ordinary builders use ordered CMG/PCG kernels,
  preserved-stopping coarse compaction, and component terminal factors. The
  standalone grounded factor remains the reference implementation. Hierarchy
  inspection now exposes `PrunedTransfer` without a feature; `aggregation()` can
  be absent on a nonterminal compact level. This candidate is on a separate
  study branch and has not been promoted on main.
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
