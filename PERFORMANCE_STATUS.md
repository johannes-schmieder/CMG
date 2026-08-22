# CMG performance status

This is the concise recovery record for the active optimization phase. Read it
together with `PERFORMANCE_PLAN.md` and the machine-readable records in
`.ci/performance/` before modifying numerical source.

## Current recovery point

- Repository head before this status refresh: `c23f53eda442b556b9c6345d7012fb7c60aeb608`.
- Latest substantive source/staging checkpoint: `dfdfb7f4fab36b44229c474731c821ecd2759313`.
- `.ci/latest.json` tested SHA: `dfdfb7f4fab36b44229c474731c821ecd2759313`.
- Formatting, Clippy, rustdoc, benchmark-crate qualification, debug/release
  tests, release build, and Ubuntu/macOS/Windows tests: `success`.
- The benchmark crate is now formatted, linted, and release-built by ordinary
  CI on every production checkpoint.

## Retained performance work

- Immutable graph/component metadata is reused across right-hand sides.
- Component and centering scratch is caller-owned and reusable.
- Terminal factors select packed or sparse traversal storage by retained bytes.
- Direct terminal assembly no longer materializes redundant dense graph buffers.
- CMG hierarchy scratch and PCG workspace were reduced substantially.
- Canonical graph edges use compact `u32` endpoints while public dimensions
  remain `usize`.
- CSR rows use compact columns and support deterministic atomics-free matvec.
- A package-owned optional Rayon pool supports deterministic setup kernels and
  memory-bounded parallel batches.
- The official pinned C matvec/restriction/prolongation/full-cycle kernels are
  compiled in the benchmark-only crate and checked for numerical agreement.
- A stable single-RHS solve benchmark now exists at
  `benchmarks/src/bin/single-rhs-solve.rs`.

## Current measured evidence

- On the available four-logical-CPU hosted runner, eight-RHS throughput is about
  `2.0x` with two threads and `2.5–2.7x` with four threads.
- The accepted optimized stationary cycle measured about `0.866x` of pinned-C
  time on a path case and `1.008x` on a worker–firm case, with quotient-space
  differences around `2.1e-12` and `1.0e-15` respectively.
- These values are directional hosted-runner evidence, not a claim about
  8–32-core or NUMA scaling.

## Active benchmark gate

The corrected one-shot gate
`.github/workflows/direct-compact-contraction.yml` is active. It evaluates
constructing coarse levels directly in retained 16-byte `Edge` storage rather
than first materializing 24-byte `(usize, usize, f64)` tuples. Its script is
`scripts/direct_compact_contraction_gate.py`.

Do not infer that this candidate was retained until
`.ci/performance/direct-compact-contraction-latest.json` exists and the workflow
and script have removed themselves. The gate must preserve hierarchy metadata,
pass all serial/all-feature tests, and meet both serial and four-thread timing
and peak-RSS limits.

## Next prepared optimization

After the contraction gate resolves, benchmark an optional
`ParallelCmgPlan` rather than adding CSR storage to the ordinary preconditioner.
The intended first checkpoint is:

1. retain deterministic CSR operators only for nonterminal levels large enough
   to use the caller's package-owned executor;
2. keep the default serial hierarchy unchanged and dependency-free;
3. parallelize CMG level matvecs, Jacobi vector kernels, and prolongation without
   changing the recursive repeat schedule;
4. validate one-thread bitwise serial behavior, multi-thread certified numerical
   agreement, hierarchy ownership, memory overhead, and stationary-cycle speed;
5. integrate the plan into the PCG outer loop only if the CMG-application gate
   produces a material end-to-end signal.

## Remaining major work

- Resolve the direct compact-contraction experiment.
- Qualify the optional parallel CMG application path.
- Integrate and benchmark single-RHS parallel PCG if the application path wins.
- Profile packed contraction keys and reusable contraction buffers.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable
  hardware; ordinary hosted runners currently expose only four logical CPUs.
- Remove obsolete self-removing workflows and staging scripts after active gates
  are secure.

## Recovery rule

Before another production source mutation, read the corresponding JSON decision
record, verify that no one-shot workflow for that experiment is still active,
and create a baseline/candidate qualification gate. Do not retain an optimization
because it merely compiles or because a staging commit exists.
