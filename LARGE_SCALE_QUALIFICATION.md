# Large-scale and 32-thread qualification

The production library accepts an explicit custom thread count through its
package-owned parallel executor. Ordinary GitHub-hosted validation currently
checks correctness and directional performance on machines with only a small
number of logical CPUs. That is sufficient to validate the parallel code path,
but it is not evidence of 8-, 16-, or 32-core scaling.

## Functional support

The all-feature test suite constructs isolated custom pools at 1, 2, 4, 8, 16,
and 32 threads. This checks that the package does not impose a four-thread cap
and that large custom pools can be created on every supported operating system.
It is deliberately not reported as a speed benchmark.

## Manual qualification workflow

`.github/workflows/manual-32-thread-qualification.yml` is a manually dispatched,
read-only workflow intended for either:

- a configured GitHub larger-runner label; or
- a controlled self-hosted runner.

The workflow:

1. checks out `main` only;
2. records machine topology and memory;
3. runs formatting, Clippy, and the release all-feature test suite;
4. builds the qualified `full-pcg-routing` benchmark;
5. runs worker-firm and dense worker-firm cases at 1, 2, 4, 8, 16, and 32
   threads when the machine exposes at least that many logical CPUs; and
6. uploads all JSON output, `/usr/bin/time -v` records, logs, and the machine
   description as a workflow artifact.

It has read-only repository permission and does not commit benchmark results.
A public-repository self-hosted runner should be reserved for manual workflows
from the protected default branch and should run under an unprivileged account.

## Required evidence

A 32-core performance claim requires all of the following from one controlled
machine and commit:

- the exact commit SHA and Rust/Cargo versions;
- CPU model, logical and physical core counts, NUMA layout, and available RAM;
- serial and parallel numerical agreement and unchanged convergence
  certification;
- setup time, CMG-application time, complete PCG time, and iteration counts;
- retained hierarchy bytes, per-RHS workspace bytes, and peak resident memory;
- repeated observations at each thread count;
- scaling for both one large RHS and many RHSs sharing one hierarchy; and
- enough graph size that parallel work dominates thread-pool and scheduling
  overhead.

## Interpretation

The preferred execution mode depends on the workload:

- **Many independent right-hand sides:** use memory-bounded across-RHS
  concurrency first. This usually has the lowest synchronization overhead.
- **One very large right-hand side:** use the selectively routed parallel CMG
  plan. Sparse path-like levels should remain serial when CSR construction and
  thread overhead do not pay for themselves.
- **Many right-hand sides on a very large graph:** use the memory-aware hybrid
  scheduler so concurrent workspaces do not exhaust RAM.

The package should not treat the maximum available CPU count as an instruction
to parallelize every level. Routing thresholds must be based on measured work,
operator density, hierarchy size, RHS count, and the configured memory budget.
