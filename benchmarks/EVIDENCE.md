# Performance evidence index

Performance work is accepted only when the candidate passes numerical
qualification and a same-run baseline/candidate gate. A staging commit is not
evidence that its source change was retained.

Machine-readable records are stored under `.ci/performance/`. Important record
families include:

- terminal-factor construction and terminal-build timing;
- hierarchy-build baselines and large-graph smoke tests;
- canonical edge construction and duplicate compaction;
- forest-splitting scratch reuse;
- compact edge endpoint feasibility and memory measurement;
- matched Rust/C sparse-kernel and recursive-cycle comparisons;
- single-solve, batch, configured-thread-count, and memory-budget checks.

`PERFORMANCE_STATUS.md` consolidates the current decision state, while
`PERFORMANCE_PLAN.md` contains the chronological live plan and the next target.
The standard `.github/workflows/ci.yml` matrix remains the source of truth for
cross-platform correctness after a retained optimization.

Hosted-runner timing is used to reject regressions and compare two binaries on
the same machine. It is not treated as proof of 32-core scaling. Such a claim
requires a manually dispatched larger runner or a controlled self-hosted
machine, with the exact graph, thread count, memory limit, compiler, and commit
recorded alongside the results.
