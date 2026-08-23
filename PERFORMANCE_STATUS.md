# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Repository head before this cleanup: `db6198ec31ccd2b23700081c785be6712a6e4f6e`.
- Latest retained numerical checkpoint:
  `701036624e312fa4a8e21a26297d8254b7dc0142`
  (`perf: retain packed endpoint-key ordering after exact gate`).
- The routed exact-capacity candidate completed full qualification but was
  rejected and reverted; production numerical source remains on the previously
  cross-platform-qualified one-pass contraction path.
- `.ci/latest.json` records quality and Ubuntu/macOS/Windows success for
  `bcc4f0ec3b46b692b055a7c5aef54de5f48768fb`, which contains the retained
  packed-key implementation. Subsequent commits changed only documentation,
  benchmark evidence, or completed one-shot automation.

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
- Canonical edge sorting compares one packed 64-bit endpoint key; exact
  allocation and retained bytes are unchanged.
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
- Packed endpoint ordering improved the original hierarchy timing gate to
  `0.970x`; the exact-allocation recheck measured `0.981x` geometrically, with
  exactly `1.000000x` additional-peak and retained requested bytes.
- The contraction survival profile found `12.0` MB
  and `10.8` MB of avoidable first-level
  reservation on path and worker–firm cases, but only
  `1.5` MB on dense worker–firm.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Latest resolved benchmark gate

The routed exact-capacity serial contraction gate completed with validation
`success` and was **not retained**. Serial geometric time ratio: 1.011; exact additional-peak ratio: 0.977.
The decision record is
`.ci/performance/exact-capacity-contraction-latest.json`.

## Next prepared optimization

The in-place unstable compact-edge sort was retained after exact hierarchy and
allocation qualification. Its geometric hierarchy-time ratio was
0.907, and its geometric exact additional-peak
ratio was 0.972.

The next checkpoint is a read-only phase-profile refresh. A custom radix path
will be considered only if contraction remains dominant after this simpler
in-place improvement.

## Remaining major work

- Extend user-facing memory/performance guidance for automatic, explicit
  within-solve, and explicit across-RHS execution.
- Identify the remaining hierarchy-setup bottleneck with read-only phase timing.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable
  hardware; ordinary hosted runners currently expose only four logical CPUs.

## Recovery rule

Before another production source mutation, read the corresponding JSON decision
record, verify that no one-shot workflow for that experiment is still active,
and create a baseline/candidate qualification gate. Do not retain an optimization
because it merely compiles or because a staging commit exists.
