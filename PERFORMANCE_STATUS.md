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

## Post-compaction routing requalification

- Status: `success`.
- Source SHA: `aef1e2354c321ce210d6f94cbabf7fffb9ba5d98`.
- Routing failures: `none`.
- Metadata failures: `none`.
- Numerical failures: `none`.
- Planned-case geometric speedup: `1.6262176840051474`.
- Maximum scaled serial/planned solution difference: `5.605302390305238e-10`.
- Evidence: `.ci/performance/post-compact-label-routing.json`.

## Contraction profile after compact labels

- Status: `success`.
- Dominant attributed phase: `sorting` (`76.2%`).
- Profiled cases: `4`.
- Exact reconstructed coarse-graph equivalence: `passed inside the benchmark for every profiled level and repetition`.
- Manual mapping uses the public compatibility label view; production totals use compact labels.
- Evidence: `.ci/performance/contraction-subphase-post-labels.json`.

## Lazy aggregation-size gate

- Decision: `retained`.
- Validation: `success`.
- Geometric retained-memory ratio: `0.953003038418093`.
- Geometric hierarchy-time ratio: `0.9983684256046577`.
- Serial/planned PCG geometric ratios: `0.9867773370546498` / `0.9951106963463812`.
- Public aggregate sizes remain available through a thread-safe lazy compatibility cache.
- Evidence: `.ci/performance/lazy-aggregation-sizes-latest.json`.

## Post-lazy-size routing requalification

- Status: `success`.
- Source SHA: `b70ba28e8e06fa7e56c4f012c9e1ad40d2ca4aa9`.
- Routing failures: `none`.
- Metadata failures: `none`.
- Numerical failures: `none`.
- Planned-case geometric speedup: `1.312632282108527`.
- Maximum scaled serial/planned solution difference: `5.605302390305238e-10`.
- Evidence: `.ci/performance/post-lazy-size-routing.json`.

## Lean forest hierarchy gate

- Decision: `retained`.
- Validation: `success`.
- Geometric / best exact additional-peak ratios: `0.967716344563314` / `0.8769852824098516`.
- Geometric hierarchy-time ratio: `0.9549888147228235`.
- Serial/planned PCG ratios: `1.0105348490677508` / `1.0070062607771673`.
- The complete public `ForestGrouping` diagnostic path is unchanged.
- Evidence: `.ci/performance/lean-forest-hierarchy-latest.json`.

## Post-lean-forest routing requalification

- Status: `success`.
- Source SHA: `ff86a5a69982edc305ef3f078b471736623a09aa`.
- Routing failures: `0`.
- Metadata failures: `0`.
- Numerical failures: `0`.
- Planned-case geometric speedup: `1.5911556967322051`.
- Maximum scaled serial/planned solution difference: `5.605302390305238e-10`.
- Evidence: `.ci/performance/post-lean-forest-routing.json`.

## Owned internal forest split gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy geometric time ratio: `0.9942737661478971`.
- Geometric / best exact additional-peak ratios: `1.0` / `1.0`.
- Public borrowed `split_forest` behavior is unchanged.
- Evidence: `.ci/performance/owned-split-forest-latest.json`.

## Owned internal component-label gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy geometric time ratio: `0.9971156999848793`.
- Geometric / best exact additional-peak ratios: `1.0` / `1.0`.
- Public `forest_components` diagnostic behavior is unchanged.
- Evidence: `.ci/performance/owned-component-labels-latest.json`.

## Parallel endpoint-first compact sort gate

- Decision: `not retained`.
- Validation: `success`.
- Active-case parallel setup geometric ratio: `0.9957837547352953`.
- Worst parallel setup / peak-RSS ratios: `1.0254294870933098` / `1.0016806860478462`.
- Serial endpoint-first ordering and public graph construction remain unchanged.
- Evidence: `.ci/performance/parallel-endpoint-sort-latest.json`.

## Density-routed parallel endpoint-first sort gate

- Decision: `not retained`.
- Validation: `success`.
- Active-case parallel setup geometric ratio: `0.9891906343831914`.
- Worst parallel setup / peak-RSS ratios: `1.0101119524801792` / `1.0042045435660512`.
- Serial endpoint-first ordering and public graph construction remain unchanged; the candidate routes only parallel compact levels with at least four edges per coarse vertex.
- Evidence: `.ci/performance/dense-parallel-endpoint-sort-latest.json`.

## Fused diagonal nonzero-count gate

- Decision: `not retained`.
- Validation: `success`.
- Graph / hierarchy geometric time ratios: `0.9956967888302344` / `1.0123246411133622`.
- Worst exact additional-peak / retained ratios: `1.0` / `1.0`.
- Edge ordering, compensated duplicate aggregation, and degree arithmetic are unchanged.
- Evidence: `.ci/performance/fused-diagonal-nnz-latest.json`.

## Trusted internal forest validation gate

- Decision: `retained`.
- Validation: `success`.
- Hierarchy geometric / worst time ratios: `0.980289811480986` / `0.9962345609157709`.
- Serial / planned PCG geometric ratios: `0.9830002420877726` / `0.984276942977803`.
- Public `split_forest` and `forest_components` validation remain unchanged.
- Evidence: `.ci/performance/trusted-forest-validation-latest.json`.

## Owned trusted forest split gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy geometric / worst time ratios: `1.0007042230394185` / `1.0152898789624596`.
- Improved hierarchy cases: `2` / 4.
- Serial / planned PCG geometric ratios: `1.0014665533844995` / `1.0022955841523074`.
- Public checked `split_forest` behavior remains unchanged.
- Evidence: `.ci/performance/owned-trusted-split-latest.json`.

## Radix endpoint-sort gate

- Decision: `not retained`.
- Validation: `success`.
- Geometric hierarchy-build ratio: `1.172x`.
- Worst peak-RSS ratio: `1.100x`.
- Evidence: `.ci/performance/radix-endpoint-sort-latest.json`.

## Sampled contraction-capacity gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy-build ratio: `0.999x`.
- Exact additional-peak ratio: `1.000x`.
- Worst process peak-RSS ratio: `1.002x`.
- Evidence: `.ci/performance/sampled-contraction-capacity-latest.json`.

## Parallel endpoint-first sort gate

- Decision: `not retained`.
- Validation: `success`.
- Worker/dense geometric ratio: `0.990x`.
- Worst setup / peak-RSS ratios: `1.026x` / `1.092x`.
- Evidence: `.ci/performance/parallel-two-stage-sort-latest.json`.

## Fused merge-diagonal gate

- Decision: `retained`.
- Validation: `success`.
- Graph-build / hierarchy-build ratios: `0.982x` / `0.982x`.
- Worst peak-RSS ratio: `1.002x`.
- Evidence: `.ci/performance/fused-merge-diagonal-latest.json`.

## Fused diagonal-statistics gate

- Decision: `retained`.
- Validation: `success`.
- Graph-build / hierarchy-build ratios: `0.981x` / `0.995x`.
- Worst peak-RSS ratio: `1.001x`.
- Evidence: `.ci/performance/fused-diagonal-statistics-latest.json`.

## Trusted compact contraction gate

- Decision: `not retained`.
- Validation: `success`.
- Production-contraction / hierarchy-build ratios: `1.005x` / `0.991x`.
- Worst peak-RSS ratio: `1.018x`.
- Evidence: `.ci/performance/trusted-compact-contraction-latest.json`.

## Incremental diagonal-metadata gate

- Decision: `not retained`.
- Validation: `success`.
- Graph-build / hierarchy-build ratios: `1.028x` / `1.009x`.
- Worst peak-RSS ratio: `1.002x`.
- Evidence: `.ci/performance/incremental-diagonal-metadata-latest.json`.

## Routed edge-buffer shrink gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy-build ratio: `1.003x`.
- Exact retained-hierarchy ratio: `1.019x`.
- Worst peak-RSS ratio: `1.016x`.
- Evidence: `.ci/performance/routed-edge-buffer-shrink-latest.json`.

## Cache-local duplicate merge gate

- Decision: `retained`.
- Validation: `success`.
- Production-contraction / hierarchy-build ratios: `0.972x` / `0.976x`.
- Worst peak-RSS ratio: `1.003x`.
- Evidence: `.ci/performance/local-duplicate-merge-latest.json`.

## Compact forest-indegree gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy-build ratios: `0.947x` / `0.980x`.
- Worst peak-RSS ratio: `1.018x`.
- Evidence: `.ci/performance/compact-forest-indegree-latest.json`.

## Compact forest-indegree exact-memory gate

- Decision: `retained`.
- Validation: `success`.
- Prior hierarchy timing ratio: `0.980x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/compact-forest-indegree-memory-latest.json`.

## Compact forest-ancestor gate

- Decision: `not retained`.
- Validation: `failure`.
- Split / hierarchy-build ratios: `1.000x` / `1.000x`.
- Worst peak-RSS ratio: `1.000x`.
- Evidence: `.ci/performance/compact-forest-ancestors-latest.json`.

## Compact forest-ancestor exact-memory gate

- Decision: `not retained`.
- Validation: `success`.
- Prior split / rerun hierarchy ratios: `0.938x` / `0.976x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/compact-forest-ancestors-memory-latest.json`.

## Two-path compact forest-ancestor gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.972x` / `0.961x`.
- Worst hierarchy RSS ratio: `1.085x`.
- Evidence: `.ci/performance/compact-forest-ancestors-two-path-latest.json`.

## Byte forest-visited gate

- Decision: `not retained`.
- Validation: `success`.
- Direct-split / hierarchy-build ratios: `0.963x` / `1.012x`.
- Exact additional-peak / worst RSS ratios: `1.000x` / `1.001x`.
- Evidence: `.ci/performance/byte-forest-visited-latest.json`.

## Direct compact forest-label gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy-build ratio: `1.011x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/direct-compact-forest-labels-latest.json`.

## Rootless forest-label gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy-build ratio: `1.011x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Worst process peak-RSS ratio: `1.009x`.
- Evidence: `.ci/performance/rootless-forest-labels-latest.json`.

## Shared Laplacian storage gate

- Decision: `retained`.
- Validation: `success`.
- Graph-build / hierarchy-build ratios: `1.008x` / `0.983x`.
- Exact additional-peak / retained ratios: `0.734x` / `0.687x`.
- Worst process peak-RSS ratio: `1.003x`.
- Evidence: `.ci/performance/shared-laplacian-storage-latest.json`.

## CSR row-count reuse gate

- Decision: `not retained`.
- Validation: `success`.
- Plan-build time / exact peak ratios: `1.008x` / `1.000x`.
- Retained plan ratio: `1.000x`.
- Evidence: `.ci/performance/reuse-csr-row-counts-latest.json`.

## Compact CSR row offsets gate

- Decision: `not retained`.
- Validation: `success`.
- Plan-build time / exact peak ratios: `1.010x` / `0.955x`.
- Retained plan ratio: `0.955x`.
- Evidence: `.ci/performance/reuse-csr-row-counts-latest.json`.

## Compact CSR row-offset full-PCG gate

- Decision: `retained`.
- Validation: `success`.
- Planned-solve / plan-size ratios: `0.989x` / `0.955x`.
- Worst planned-solve / process-RSS ratios: `0.997x` / `1.004x`.
- Evidence: `.ci/performance/compact-csr-row-offsets-pcg-latest.json`.

## Forest phase profile after ownership and compaction work

- Status: `success`.
- Serial dominant phase: `split` (56.8%).
- Executor-aware dominant phase: `split` (51.6%).
- Cross-mode target selected for the next gate: `split`.
- Evidence: `.ci/performance/forest-phase-profile-latest.json`.

## Post-conductance forest-split subphase profile

- Status: `success`.
- Dominant subphase: `diameter` (63.1%).
- Allocation / indegree / diameter / conductance shares: `5.4%` / `10.4%` / `63.1%` / `21.2%`.
- Evidence: `.ci/performance/split-forest-subphase-profile-post-conductance.json`.

## Inline forest-walk gate

- Decision: `not retained`.
- Validation: `success`.
- Trusted-split / hierarchy-build ratios: `0.981x` / `1.044x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/inline-forest-walk-latest.json`.

## Recomputed forest ancestor-prefix gate

- Decision: `not retained`.
- Validation: `success`.
- Trusted-split / hierarchy-build ratios: `0.821x` / `0.941x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-latest.json`.

## Recomputed ancestor-prefix RSS requalification

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy-time / median-RSS ratios: `0.971x` / `1.035x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-rss-latest.json`.

## Internal serial heavy-edge routing gate

- Decision: `not retained`.
- Validation: `success`.
- Worker/dense hierarchy-build ratio: `0.861x`.
- Worst median-RSS ratio: `1.044x`.
- Evidence: `.ci/performance/internal-serial-heavy-edge-latest.json`.

## Owned forest-split gate

- Decision: `not retained`.
- Validation: `failure`.
- Hierarchy-time / exact additional-peak ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/owned-forest-split-latest.json`.

## Branch-free conductance-pass gate

- Decision: `retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.962x` / `0.981x`.
- Worst peak-RSS ratio: `1.001x`.
- Evidence: `.ci/performance/split-conductance-branch-latest.json`.

## Bounded ancestor-prefix gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.918x` / `0.966x`.
- Worst peak-RSS ratio: `1.090x`.
- Evidence: `.ci/performance/bounded-ancestor-prefix-latest.json`.

## Bounded ancestor-prefix exact-memory gate

- Decision: `not retained`.
- Validation: `success`.
- Prior split / rerun hierarchy ratios: `0.918x` / `0.985x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Median RSS geometric / worst ratios: `1.013x` / `1.051x`.
- Evidence: `.ci/performance/bounded-ancestor-prefix-memory-latest.json`.

## Bounded ancestor-prefix path-scaling gate

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy timing ratio: `0.956x`.
- Maximum RSS delta / largest-case ratio: `4008` KiB / `1.000x`.
- Evidence: `.ci/performance/bounded-prefix-path-rss-latest.json`.

## Bounded ancestor-prefix large-path confirmation

- Decision: `not retained`.
- Validation: `success`.
- Hierarchy timing ratio: `0.954x`.
- 4m / 8m RSS ratios: `1.000x` / `1.013x`.
- Evidence: `.ci/performance/bounded-prefix-large-path-confirmation.json`.

## Branchless ancestor-recording gate

- Decision: `retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.908x` / `0.985x`.
- Worst peak-RSS ratio: `1.001x`.
- Evidence: `.ci/performance/branchless-ancestor-recording-latest.json`.

## Post-branchless forest-split subphase profile

- Status: `success`.
- Dominant subphase: `diameter` (62.2%).
- Allocation / indegree / diameter / conductance shares: `5.4%` / `10.1%` / `62.2%` / `22.3%`.
- Evidence: `.ci/performance/split-forest-subphase-profile-v3-latest.json`.

## Zipped ancestor-update gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.992x` / `0.997x`.
- Worst peak-RSS ratio: `1.001x`.
- Evidence: `.ci/performance/zipped-ancestor-updates-latest.json`.

## Branch-free diameter-front gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `1.007x` / `0.995x`.
- Worst peak-RSS ratio: `1.001x`.
- Evidence: `.ci/performance/diameter-front-control-latest.json`.

## Diameter traffic profile

- Status: `success`.
- Parent steps per profiled vertex: `1.263`.
- Revisit share: `35.470%`.
- Counted parent-step / ancestor-update shares: `48.9%` / `51.1%`.
- Maximum walk length: `9`.
- Evidence: `.ci/performance/diameter-traffic-profile-latest.json`.

## Packed forest visit-map gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `1.071x` / `1.026x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/packed-forest-visit-map-latest.json`.

## Fused walk/ancestor scratch gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.887x` / `0.985x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/fused-walk-ancestor-scratch-latest.json`.

## Fused walk/ancestor scratch confirmation

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.905x` / `0.981x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/fused-walk-ancestor-scratch-confirmation.json`.

## Compact walk/ancestor scratch gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.887x` / `0.996x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/compact-walk-ancestor-scratch-latest.json`.

## Inline walk/ancestor scratch gate

- Decision: `not retained`.
- Validation: `success`.
- Split / hierarchy ratios: `0.985x` / `1.007x`.
- Exact additional-peak / retained ratios: `1.000x` / `1.000x`.
- Evidence: `.ci/performance/inline-walk-ancestor-scratch-latest.json`.
