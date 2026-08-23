# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Latest retained numerical checkpoint:
  `19220a4ea31c0869e8a4ab9c7190f6cb79b42bba`
  (`perf: retain robust prepared parallel PCG solver`).
- The robust retain gate passed complete serial/all-feature qualification,
  numerical equivalence, bounded-workspace checks, and interleaved seven-sample
  strategy benchmarks on Ubuntu.
- `.ci/latest.json` still names the pre-retention checkpoint
  `99e8cdcc8b2c7be0523d03c78d6405e79cd83317`; this status update intentionally
  triggers ordinary formatting, Clippy, rustdoc, benchmark-crate qualification,
  debug/release tests, release build, and Ubuntu/macOS/Windows testing on the
  exact retained source.
- Do not begin another production numerical mutation until `.ci/latest.json`
  records the retained checkpoint with quality and cross-platform status
  `success`.

## Retained performance work

- Immutable graph/component metadata is reused across right-hand sides.
- Component and centering scratch is caller-owned and reusable.
- Terminal factors select packed or sparse traversal storage by retained bytes.
- Direct terminal assembly no longer materializes redundant dense graph buffers.
- CMG hierarchy scratch and PCG workspace were reduced substantially.
- Canonical graph edges use compact `u32` endpoints while public dimensions
  remain `usize`.
- Graph construction compacts validated duplicate edges in retained 16-byte
  storage and trims unused capacity before levels are retained.
- Coarse contraction constructs retained 16-byte `Edge` values directly,
  avoiding temporary 24-byte `(usize, usize, f64)` tuples in serial and
  parallel hierarchy construction.
- CSR rows use compact columns and support deterministic atomics-free matvec.
- A package-owned optional Rayon pool supports deterministic setup kernels and
  memory-bounded parallel batches.
- The optional `ParallelCmgPlan` stores CSR operators only on routed
  nonterminal levels. Sparse path-like cases retain the serial hierarchy and
  build no parallel operators.
- Planned PCG reuses one validated plan and caller-owned workspace through every
  Krylov iteration, including fresh residual replacement and final
  original-system certification.
- `ParallelPcgSolver` now owns one immutable graph/preconditioner/executor/plan
  bundle, reports retained plan and workspace costs, and routes a batch to
  serial, within-solve planned, or across-RHS execution without nested
  oversubscription.
- Automatic batch workspaces retain exactly the memory implied by the selected
  concurrency; the robust gate measured zero excess bytes.
- Existing explicit serial, planned, and across-RHS APIs remain available.
- The official pinned C matvec/restriction/prolongation/full-cycle kernels are
  compiled in the benchmark-only crate and checked for numerical agreement.

## Current measured evidence

- On the available four-logical-CPU hosted runner, eight-RHS throughput is about
  `2.0x` with two threads and `2.5–2.7x` with four threads.
- The accepted optimized stationary cycle measured about `0.866x` of pinned-C
  time on a path case and `1.008x` on a worker–firm case, with quotient-space
  differences around `2.1e-12` and `1.0e-15` respectively.
- Direct compact contraction passed six million-scale serial and four-thread
  cases. Its geometric hierarchy-build time and peak-RSS ratios were
  `0.952x` and `0.924x`; the parallel-case ratios were `0.942x` and `0.883x`.
- Selectively routed `ParallelCmgPlan` application had four-thread geometric
  speedup `1.279x` with zero scaled numerical difference.
- Complete certified planned-PCG solves had four-thread geometric speedup
  `1.237x`, with identical iteration counts, residuals, backward errors, and
  zero measured solution difference in every retained case.
- The robust prepared-solver gate used seven interleaved samples per strategy.
  Automatic-to-best explicit timing was `0.989x` geometrically and at worst
  `1.054x`, inside the unchanged `1.04x`/`1.12x` gates.
- Automatic-to-selected explicit timing was `0.986x` geometrically and at worst
  `0.999x`, inside the stricter `1.03x`/`1.08x` gates.
- Maximum scaled solution difference and retained workspace excess were both
  exactly zero in the benchmark matrix.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Latest resolved benchmark gate

The robust memory-aware prepared parallel PCG solver passed validation and was
**retained**. Its decision record is
`.ci/performance/prepared-parallel-solver-robust-latest.json`.

## Next prepared optimization

After ordinary cross-platform qualification closes, profile hierarchy setup
allocation and bandwidth again with the direct compact-contraction path in
place. The next retain/revert experiments should be:

1. packed 64-bit endpoint keys for coarse-edge sorting where both aggregate
   endpoints fit in `u32`;
2. reusable coarse-contraction buffers that do not increase retained hierarchy
   memory or compromise concurrent builders;
3. compact aggregation-label storage only if the public API can remain
   compatible without eagerly duplicating native-width labels;
4. retain each change only after serial and four-thread timing, exact hierarchy,
   peak-memory, and full numerical gates pass.

## Remaining major work

- Complete ordinary Ubuntu/macOS/Windows qualification of the retained prepared
  solver source.
- Add user-facing examples and memory/performance guidance for automatic,
  explicit within-solve, and explicit across-RHS execution.
- Profile packed contraction keys and reusable contraction buffers.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable
  hardware; ordinary hosted runners currently expose only four logical CPUs.
- Remove obsolete self-removing workflows, staging scripts, and committed
  Python cache files after active gates are secure.

## Recovery rule

Before another production source mutation, read the corresponding JSON decision
record, verify that no one-shot workflow for that experiment is still active,
and create a baseline/candidate qualification gate. Do not retain an optimization
because it merely compiles or because a staging commit exists.
