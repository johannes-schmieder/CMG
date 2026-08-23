# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Repository head before this status refresh:
  `ebc7b69a8b175edf7758e574ed32d36ef0fc6efb`.
- Latest substantive numerical checkpoint:
  `ebc7b69a8b175edf7758e574ed32d36ef0fc6efb`
  (`perf: add selectively routed parallel CMG plan`).
- The retain gate passed full serial/all-feature validation on Ubuntu. This
  status refresh intentionally triggers ordinary formatting, Clippy, rustdoc,
  benchmark-crate qualification, debug/release tests, release build, and
  Ubuntu/macOS/Windows testing on the exact retained source.
- Do not begin the PCG integration mutation until `.ci/latest.json` records
  this checkpoint with quality and cross-platform status `success`.

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
- The official pinned C matvec/restriction/prolongation/full-cycle kernels are
  compiled in the benchmark-only crate and checked for numerical agreement.
- Stable stationary-apply and single-RHS solve benchmarks are retained in the
  benchmark crate.

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
  speedup `1.279x` with zero scaled numerical difference. It built no path
  operator, measured `1.166x` on the 600,000-edge worker–firm case, and
  `2.358x` on the 800,000-edge dense worker–firm case.
- Optional plan storage remained below the configured 128 bytes per original
  edge, with a measured maximum of about 112.71 bytes per edge.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Latest resolved benchmark gate

The broad parallel-plan candidate was rejected because it slowed the path case
below its hard floor. The refined router then passed validation and was
**retained**. Its decision record is
`.ci/performance/parallel-cmg-routed-plan-latest.json`.

## Next prepared optimization

After the ordinary cross-platform checkpoint closes, integrate the optional
plan into a separate parallel PCG path without changing the existing serial API:

1. reuse one immutable `ParallelCmgPlan` and caller-owned workspace throughout
   all PCG iterations;
2. retain fresh original-system residual replacement and final certification;
3. route graph matvec and CMG application independently so sparse/path cases
   can remain serial;
4. benchmark full end-to-end solves, not only stationary applications;
5. retain the new public path only if iteration counts, certificates, memory,
   and per-family runtime gates all pass.

## Remaining major work

- Complete ordinary Ubuntu/macOS/Windows qualification of the retained routed
  plan.
- Implement and qualify an opt-in single-RHS parallel PCG path.
- Benchmark thread counts 1, 2, and 4 in the hosted environment and preserve an
  explicit user thread-count control for future 8–32-core qualification.
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
