# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Repository head before this status refresh:
  `f3b3a7acd0181f5872ebf0d79ac7f9e67cffb8fa`.
- Latest substantive numerical checkpoint:
  `f3b3a7acd0181f5872ebf0d79ac7f9e67cffb8fa`
  (`perf: add opt-in prebuilt-plan parallel PCG`).
- The retain gate passed complete serial/all-feature qualification and
  end-to-end certified-solve benchmarks on Ubuntu. This status refresh
  intentionally triggers ordinary formatting, Clippy, rustdoc,
  benchmark-crate qualification, debug/release tests, release build, and
  Ubuntu/macOS/Windows testing on the exact retained source.
- Do not begin another production numerical mutation until `.ci/latest.json`
  records this checkpoint with quality and cross-platform status `success`.

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
- The opt-in planned PCG API reuses one validated plan and caller-owned
  workspace through every Krylov iteration, including scheduled fresh residual
  replacement and final original-system certification. The existing serial PCG
  API and implementation remain unchanged.
- The official pinned C matvec/restriction/prolongation/full-cycle kernels are
  compiled in the benchmark-only crate and checked for numerical agreement.
- Stable stationary-apply, serial-solve, and planned-PCG benchmarks are retained
  in the benchmark crate.

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
- Complete certified planned-PCG solves had four-thread geometric speedup
  `1.237x`, with identical iteration counts, residuals, backward errors, and
  zero measured solution difference in every retained case.
- Full-solve speedups were `2.169x` on the 800,000-edge dense worker–firm case,
  `1.066x` on the 600,000-edge worker–firm case, `1.026x` on the 300,000-edge
  worker–firm case, and `0.988x` on the path, which built no parallel operator.
- Measured plan-build break-even was below one RHS on the dense and larger
  worker–firm cases and about 1.70 RHS on the smaller worker–firm case.
- Optional plan storage remained below the configured 128 bytes per original
  edge, with a measured maximum of about 112.71 bytes per edge.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Latest resolved benchmark gate

The opt-in prebuilt-plan PCG candidate passed validation and was **retained**.
Its decision record is `.ci/performance/parallel-pcg-latest.json`.

## Next prepared optimization

After ordinary cross-platform qualification closes, build a prepared solver
abstraction and benchmark a memory-aware hybrid policy for repeated right-hand
sides:

1. combine one immutable preconditioner, executor, and optional parallel plan
   behind a reusable solver object without hiding memory costs;
2. choose between within-solve planned PCG and across-RHS serial PCG according
   to RHS count, routed operator count, thread count, and workspace budget;
3. prevent nested oversubscription by assigning each CPU to exactly one level
   of parallelism;
4. benchmark RHS counts 1, 2, 4, 8, and 32 at 1, 2, and 4 threads;
5. preserve input order, original-system certification, and existing APIs;
6. retain automatic routing only if it is never materially worse than the best
   explicit strategy on the qualified workload matrix.

## Remaining major work

- Complete ordinary Ubuntu/macOS/Windows qualification of the retained planned
  PCG source.
- Add user-facing examples and memory/performance guidance for serial,
  within-solve, and across-RHS execution.
- Qualify the prepared/hybrid repeated-RHS strategy.
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
