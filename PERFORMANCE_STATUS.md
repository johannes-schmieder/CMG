# CMG performance status

This is the concise recovery record for the active optimization phase. Read it together with `PERFORMANCE_PLAN.md` and the machine-readable records in `.ci/performance/` before modifying numerical source.

## Qualified repository state

- Latest fully tested source-and-test checkpoint: `decd50568508d83e78a84dd35938029ca39a206e`.
- Formatting, Clippy, rustdoc, benchmark-crate qualification, debug/release tests, release build, and Ubuntu/macOS/Windows tests: `success`.
- The dependency-free serial build and the optional `parallel` feature remain supported.
- CI status publishing rebuilds from the latest `origin/main`, preventing bookkeeping commits from displacing numerical or benchmark checkpoints.

## Retained implementation

- Deterministic stationary CMG hierarchy and certified quotient-space PCG.
- Reused immutable graph/component metadata and allocation-free repeated stationary application.
- Compressed terminal factors, direct terminal assembly, and reduced CMG/PCG workspaces.
- Checked compact `u32` retained edge endpoints with public `usize` dimensions.
- Trimmed compact graph construction, packed endpoint keys, unstable total-order sorting, and direct compact coarse contraction.
- Deterministic CSR row operators for selected parallel hierarchy levels.
- Package-owned optional Rayon executor, selectively routed `ParallelCmgPlan`, plan-aware certified PCG, and memory-bounded multi-RHS scheduling.
- Benchmark-only differential comparison against the pinned official C kernels.

## Qualified full-PCG router

The default prepared single-RHS router uses planned execution from **350,000
canonical retained edges** when a parallel hierarchy operator exists and more
than one thread is available. The threshold remains overridable through
`ParallelPcgPolicy`.

- Routing-matrix SHA: `8749fa228e0b99f555a2a5e3838c1ac8843a9cea`; run `32649078880`.
- Status: `success`.
- Numerical failures: `none`.
- Routing mismatches: `none`.
- Maximum scaled serial/planned solution difference:
  `0.000e+00`.
- Geometric planned speedup: `1.208x`.

| Case | Input edges | Canonical edges | Auto route | Planned speedup | Iterations |
|---|---:|---:|---|---:|---:|
| dense-worker-firm-1.6m | 1,600,000 | 1,599,978 | Planned | 1.777x | 9 / 9 |
| dense-worker-firm-400k | 400,000 | 399,930 | Planned | 1.361x | 9 / 9 |
| dense-worker-firm-600k | 600,000 | 599,918 | Planned | 1.446x | 9 / 9 |
| dense-worker-firm-800k | 800,000 | 799,978 | Planned | 1.491x | 9 / 9 |
| path-250k | 249,999 | 249,999 | Serial | 0.995x | 29 / 29 |
| worker-firm-300k | 300,000 | 299,996 | Serial | 1.020x | 20 / 20 |
| worker-firm-375k | 375,000 | 374,996 | Planned | 1.041x | 20 / 20 |
| worker-firm-450k | 450,000 | 449,996 | Planned | 1.038x | 20 / 20 |
| worker-firm-525k | 525,000 | 524,996 | Planned | 1.059x | 20 / 20 |
| worker-firm-600k | 600,000 | 599,996 | Planned | 1.091x | 19 / 19 |

These are directional measurements from an ordinary four-logical-CPU hosted
runner, not a claim about 8–32-core or NUMA scaling.

## Other retained evidence

- Direct compact contraction: geometric hierarchy time `0.952x`; geometric peak RSS `0.924x` across large serial/parallel cases.
- Accepted stationary cycle versus pinned C: about `0.866x` of C time on a path case and `1.008x` on a worker–firm case, with quotient-space differences around `2.1e-12` and `1.0e-15`.
- Eight-RHS throughput on the available runner: approximately `2.0x` with two threads and `2.5–2.7x` with four threads.
- The latest hierarchy phase profile identifies coarse contraction as roughly `72.4%` of attributed setup time overall, including about `65.1%` on worker–firm and `81.9%` on dense worker–firm graphs; path setup is instead dominated by forest splitting.

## Current recovery point

Single-RHS routing and threshold qualification are complete. The next performance gate is a read-only phase profile of the planned PCG outer loop. The planned path already parallelizes selected CMG applications and finest-level matvecs; dot products, norms, Krylov vector updates, component centering, and residual reconstruction remain mostly serial. No outer-PCG parallelization should be retained without an end-to-end certified solve improvement.

## Remaining major work

- Measure the full planned-PCG time share attributable to preconditioner application, matvec, reductions, vector updates, component centering, and residual certification.
- Benchmark fixed-chunk deterministic parallel dot products, norms, and vector updates only if the profile shows material headroom.
- Continue coarse-contraction/setup profiling, particularly reusable contraction buffers and routed temporary capacity.
- Obtain controlled 8-, 16-, and 32-thread/high-memory evidence on suitable hardware.
- Defer panel Krylov, aggressive SIMD, and NUMA-specific tuning until ordinary deterministic paths are fully profiled.

## Certified PCG phase profile

- Minimum CMG/matvec share: `47.4%`.
- Maximum serial outer-PCG share: `49.7%`.
- Maximum vector-update share: `2.7%`.
- Evidence: `.ci/performance/pcg-phase-profile-latest.json`.

## Single-component centering gate

- Decision: `retained`.
- Validation: `success`.
- Geometric full-solve ratio: `0.827x`.
- Evidence: `.ci/performance/single-component-centering-latest.json`.

## Fused centering and norm-scale gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric full-solve ratio: `1.004x`.
- Evidence: `.ci/performance/fused-centering-norm-latest.json`.

## Parallel outer-PCG centering gate

- Decision: `not retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `1.025x`.
- Evidence: `.ci/performance/parallel-outer-centering-latest.json`.

## Fused centering and preconditioned-dot gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric full-solve ratio: `0.996x`.
- Evidence: `.ci/performance/fused-centering-dot-latest.json`.

## Exact parallel norm-scale gate

- Decision: `retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `0.971x`.
- Evidence: `.ci/performance/parallel-exact-norm-scale-latest.json`.

## Deterministic fixed-chunk dot gate

- Decision: `retained`.
- Validation: `success`.
- Planned geometric full-solve ratio: `0.960x`.
- Maximum scaled solution difference: `2.362e-10`.
- Evidence: `.ci/performance/fixed-chunk-dot-latest.json`.

## Production-reduction profiler sync

- Decision: `not retained`.
- Validation: `not_run`.
- The phase profiler reuses the exact production planned-PCG dot and norm helpers.
- Evidence: `.ci/performance/pcg-profiler-sync.json`.

## Profiler synchronization fallback

- The profiler synchronization candidate was not retained.
- The unchanged production solver passed the complete baseline suite.
- Independent full-PCG timing gates continue without relying on the stale profiler.
- Evidence: `.ci/performance/pcg-profiler-sync.json`.

## Forest-backed contraction capacity gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric hierarchy-time ratio: `1.0066420586495628`.
- Geometric exact additional-peak ratio: `0.992011742241337`.
- Production uses the hint only for CMG forest aggregations; the public generic aggregation path is unchanged.
- Evidence: `.ci/performance/forest-capacity-hint-latest.json`.

## Corrected two-stage contraction sort gate

- Decision: `not retained`.
- Validation: `failure`.
- Worker-firm geometric hierarchy-time ratio: `n/a`.
- Worst case hierarchy-time ratio: `n/a`.
- Parallel sorting remains unchanged; the candidate affects only serial compact coarse-edge ordering.
- Evidence: `.ci/performance/two-stage-sort-v2-latest.json`.

## Function-scoped two-stage contraction sort gate

- Decision: `not retained`.
- Validation: `failure`.
- Worker-firm geometric hierarchy-time ratio: `n/a`.
- Worst hierarchy-time ratio: `n/a`.
- Parallel sort behavior is unchanged.
- Evidence: `.ci/performance/two-stage-sort-v3-latest.json`.

## Function-scoped two-stage contraction sort gate

- Decision: `retained`.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.9498827762234192`.
- Worst hierarchy-time ratio: `0.9802562268388151`.
- Parallel sort behavior is unchanged.
- Evidence: `.ci/performance/two-stage-sort-v4-latest.json`.

## One-pass compensated duplicate merge gate

- Decision: `retained`.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.9775259095049927`.
- Worst hierarchy-time ratio: `0.9835428271471965`.
- Evidence: `.ci/performance/one-pass-merge-latest.json`.

## Current-kernel contraction subphase profile

- Exact production equivalence: `passed`.
- Mapping share: `11.2%`.
- Sorting share: `74.8%`.
- Duplicate-merging share: `8.7%`.
- Diagonal share: `4.9%`.
- Finalization share: `0.4%`.
- Evidence: `.ci/performance/contraction-subphase-profile-v2.json`.

## Raw graph two-stage ordering gate

- Decision: `not retained`.
- Validation: `success`.
- Overall geometric graph-build ratio: `1.00652839386318`.
- Duplicate-heavy geometric graph-build ratio: `0.9707700491430172`.
- The parallel sort path remains unchanged.
- Evidence: `.ci/performance/raw-two-stage-sort-latest.json`.

## Sample-routed raw graph ordering gate

- Decision: `not retained`.
- Validation: `failure`.
- Overall geometric graph-build ratio: `n/a`.
- Duplicate-heavy geometric graph-build ratio: `n/a`.
- The parallel sort path remains unchanged.
- Evidence: `.ci/performance/routed-raw-sort-latest.json`.

## Routed compact-edge radix sort gate

- Decision: `not retained`.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.909923414399904`.
- Worst hierarchy-time ratio: `1.0134175253014959`.
- Worst exact additional-peak ratio: `1.1128925174302333`.
- Parallel sorting is unchanged; the candidate affects only large serial/fallback compact coarse-edge ordering.
- Evidence: `.ci/performance/radix-compact-sort-latest.json`.

## Density-routed compact-edge radix sort gate

- Decision: `not retained`.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.9380082112784894`.
- Worst hierarchy-time ratio: `1.0359722535557985`.
- Worst exact additional-peak ratio: `1.1231767791030263`.
- Parallel sorting is unchanged; scratch radix is restricted to large moderate-density or bounded small-dense serial/fallback levels.
- Evidence: `.ci/performance/routed-radix-compact-sort-latest.json`.

## Moderate-density compact-edge radix sort gate

- Decision: `not retained`.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.958719162116395`.
- Worst hierarchy-time ratio: `0.9966956961782049`.
- Worst exact additional-peak ratio: `1.0610772414320586`.
- Parallel sorting is unchanged; scratch radix is restricted to large serial/fallback levels with two to eight mapped edges per coarse vertex.
- Evidence: `.ci/performance/moderate-radix-compact-sort-latest.json`.

## Compact aggregation-label gate

- Decision: `not retained`.
- Validation: `failure`.
- Geometric retained-memory ratio: `n/a`.
- Geometric hierarchy-time ratio: `n/a`.
- Serial/planned PCG geometric ratios: `n/a` / `n/a`.
- Public native-width labels remain available through a lazy compatibility cache.
- Evidence: `.ci/performance/compact-aggregation-labels-latest.json`.

## Corrected compact aggregation-label gate

- Decision: `retained`.
- Validation: `success`.
- Geometric retained-memory ratio: `0.956863044090649`.
- Geometric hierarchy-time ratio: `0.9934997461358874`.
- Serial/planned PCG geometric ratios: `0.995387475894316` / `0.9878997999485909`.
- Public native-width labels remain available through a lazy compatibility cache.
- Evidence: `.ci/performance/compact-aggregation-labels-v2-latest.json`.
