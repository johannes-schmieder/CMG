# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Repository head before this status refresh: `7f2e2d62e5a5e2922a19013c749daa5638a92753`.
- Latest substantive numerical checkpoint: `1f94d86c93a1fd5ccd2e4a9ca590c5b8cb197b77`
  (`perf: retain direct compact contraction`).
- The latest ordinary three-platform record currently names
  `4ec2cde67cb8d4d4d6577ae8eb981445f2e5737b`; it passed formatting, Clippy,
  rustdoc, benchmark-crate qualification, debug/release tests, release build,
  and Ubuntu/macOS/Windows tests, but predates the retained numerical commit.
- This status refresh intentionally triggers ordinary CI on the exact retained
  source state. Do not begin another production source mutation until
  `.ci/latest.json` records this checkpoint with both quality and
  cross-platform status `success`.

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
- Coarse contraction now constructs retained 16-byte `Edge` values directly,
  avoiding temporary 24-byte `(usize, usize, f64)` tuples in serial and
  parallel hierarchy construction.
- CSR rows use compact columns and support deterministic atomics-free matvec.
- A package-owned optional Rayon pool supports deterministic setup kernels and
  memory-bounded parallel batches.
- The official pinned C matvec/restriction/prolongation/full-cycle kernels are
  compiled in the benchmark-only crate and checked for numerical agreement.
- A stable single-RHS solve benchmark exists at
  `benchmarks/src/bin/single-rhs-solve.rs`.

## Current measured evidence

- On the available four-logical-CPU hosted runner, eight-RHS throughput is about
  `2.0x` with two threads and `2.5–2.7x` with four threads.
- The accepted optimized stationary cycle measured about `0.866x` of pinned-C
  time on a path case and `1.008x` on a worker–firm case, with quotient-space
  differences around `2.1e-12` and `1.0e-15` respectively.
- Direct compact contraction passed six million-scale serial and four-thread
  cases. Its geometric hierarchy-build time and peak-RSS ratios were
  `0.952x` and `0.924x`; the parallel-case ratios were `0.942x` and `0.883x`.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Latest resolved benchmark gate

The corrected direct compact-contraction gate completed with validation
`success`, and the candidate was **retained**. The decision record is
`.ci/performance/direct-compact-contraction-latest.json`.

## Next prepared optimization

After the cross-platform checkpoint closes, benchmark an optional
`ParallelCmgPlan` rather than adding CSR storage to the ordinary preconditioner.
The intended first checkpoint is:

1. retain deterministic CSR operators only for nonterminal levels large enough
   to use the caller's package-owned executor;
2. keep the default serial hierarchy unchanged and dependency-free;
3. parallelize CMG level matvecs, Jacobi vector kernels, and prolongation without
   changing the recursive repeat schedule;
4. validate one-thread serial behavior, multi-thread certified numerical
   agreement, hierarchy ownership, memory overhead, and stationary-cycle speed;
5. integrate the plan into the PCG outer loop only if the CMG-application gate
   produces a material end-to-end signal.

## Remaining major work

- Complete ordinary Ubuntu/macOS/Windows qualification of the retained direct
  contraction source.
- Rebase and qualify the optional parallel CMG application path against the
  now-optimized contraction implementation.
- Integrate and benchmark single-RHS parallel PCG only if the application path
  wins under selective routing.
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
