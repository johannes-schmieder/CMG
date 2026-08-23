# CMG Performance Optimization Plan

This is the live recovery, measurement, and decision log for the performance phase of the Rust CMG port. It is updated at every substantive checkpoint on `main`.

## Baselines and pinned references

- Correctness baseline: `b65ae28a15f00925348046bb474c8133e5128cd0`
- Frozen benchmark harness/numerical baseline: `b45b252f88925028e3ad9a73a3f75eeab05f6754`
- Pinned official CMG source: `19752fc102f8cae8e34f66457bfaccb1aaa60375`

Retained records:

- `.ci/latest.json`: format, Clippy, rustdoc, debug/release, and three-platform qualification.
- `.ci/performance/latest.json`: same-run frozen-baseline versus current serial benchmarks.
- `.ci/performance/parallel-latest.json`: hosted thread scaling and memory-bounded batch results.
- `.ci/performance/c-kernel-latest.json`: pinned standalone C kernel comparison.
- `.ci/performance/cycle-wiring-latest.json`: complete iterative stationary-cycle differential result.
- `.ci/performance/compatible-apply-latest.json`: accepted compatible-RHS stationary-core experiment.
- `.ci/performance/inplace-level-output-latest.json`: accepted caller-output hierarchy workspace experiment.
- `.ci/performance/recursive-centering-latest.json`: accepted internal coarse-residual centering experiment.
- `.ci/performance/compact-centering-metadata-latest.json`: accepted coarse-centering metadata experiment.
- `.ci/performance/prevalidated-pcg-apply-latest.json`: rejected prevalidated PCG apply experiment.
- `.ci/performance/terminal-factor-latest.json`: accepted direct terminal-factor assembly experiment.
- `.ci/performance/inplace-edge-compaction-latest.json`: rejected in-place edge-compaction experiment.
- `.ci/performance/hierarchy-allocation-probe.json`: benchmark-only exact requested-byte hierarchy allocation probe.

Hosted-runner timings are directional. Claims about 8–32-thread scaling, NUMA behavior, or very large memory configurations require a larger or self-hosted runner.

## Goals

1. Reduce hierarchy setup and repeated-solve time without weakening original-system residual certification.
2. Scale useful work across machines with 1–32 or more logical CPUs.
3. Keep hierarchy and per-RHS workspace memory predictable on very large graphs.
4. Preserve deterministic hierarchy construction and reproducible numerical behavior.
5. Optimize both one very large solve and many right-hand sides sharing one hierarchy.
6. Compare Rust hot paths with the pinned official C implementation wherever standalone comparison is possible.

## Numerical rules

- No hidden ridge, graph mutation, tolerance relaxation, or silent solver substitution.
- Public-boundary validation and final original-system residual certification remain mandatory.
- Every retained numerical change must pass serial and all-feature debug/release tests.
- Production checkpoints are qualified on Ubuntu, macOS, and Windows.
- Parallel sparse kernels use row/gather ownership rather than atomics or one full output vector per thread.
- The dependency-free serial build remains supported and warning-free.
- Timing is interpreted only after hierarchy diagnostics, iteration counts, C parity where available, and residual certificates are checked.

## Phase status

| Phase | Status | Gate |
|---|---|---|
| P0 Measurement | COMPLETE | Matched baseline/current records are retained |
| P1 Repeated overhead | COMPLETE | Graph metadata and compatible stationary application are reused |
| P2 Terminal/workspace memory | COMPLETE | Factor compression and scratch aliasing pass qualification |
| P3 Frozen CSR | COMPLETE | Deterministic CSR agrees with edge matvec; measured routing is documented |
| P4 Parallel solve kernels | COMPLETE | Selective row-parallel CMG and plan-aware certified PCG pass qualification |
| P5 Multi-RHS scheduler | COMPLETE | Ordered memory-bounded concurrent solves pass cross-platform tests |
| P6 Parallel setup | COMPLETE | Sorting, direct compact contraction, and heavy-edge routing passed large hosted gates |
| P7 Pinned C comparison | COMPLETE | Matvec, restriction, prolongation, and full iterative cycle agree and are timed |
| P8 Large qualification | PARTIAL | Hosted 1–4-thread evidence exists; controlled 8–32-thread/high-memory evidence remains |
| P9 Panel/SIMD/NUMA work | DEFERRED | Begin only after full-PCG profiles justify the complexity |

## Implemented performance work

### Repeated solves and immutable metadata

- Cloned graphs use a private lineage token for constant-time normal compatibility checks, with structural fallback for independently rebuilt equal graphs.
- Connected components for every hierarchy level are computed once and retained by the preconditioner.
- Component projection and centering reuse persistent work arrays.
- `matrix_nnz` and the operator-norm bound are computed once during graph construction and returned in constant time.
- The normal repeated-RHS path no longer reconstructs components or rescans graph diagonals for immutable invariants.
- `CmgPreconditioner::apply_compatible_into` exposes the stationary core for callers that have already established component compatibility.
- PCG projects the submitted RHS once, maintains residuals in the quotient space, removes only accumulated component-nullspace roundoff, and reuses the compatible stationary core on later applications.
- Recursive coarse residuals use deterministic component centering instead of repeating public-boundary compatibility validation, stable-representative correction, and projection-norm work.

The compatible-RHS change was retained at commit `6d5f4cca` after same-run validation of debug/release tests, C-cycle parity, iteration counts, and original-system residual certificates. Relative to the immediately preceding production code:

| Case | Stationary-cycle time | Full PCG solve per RHS | Iterations |
|---|---:|---:|---:|
| Path | `0.697x` | `0.823x` | 26 → 26 |
| Worker–firm | `0.621x` | `0.894x` | 20 → 20 |

The recursive-centering change was retained at commit `f6378554` after the same numerical gates. Relative to the in-place-output baseline immediately before it:

| Case | Stationary-cycle time | Full PCG solve per RHS | Iterations |
|---|---:|---:|---:|
| Path | `0.730x` | `0.869x` | 26 → 26 |
| Worker–firm | `0.712x` | `0.944x` | 20 → 20 |

Backward errors changed only at floating-point roundoff scale. The full-cycle quotient-space differences from pinned C remained about `2.1e-12` on the path case and approximately `1.0e-15` on worker–firm.

The compact coarse-centering metadata experiment was retained after cross-checking hierarchy structure, iterations, backward errors, and the pinned-C stationary cycle. Candidate metadata was `0.623x` and `0.794x` of the old label-only lower bound on path and worker–firm cases. The same-run geometric-mean solve ratio was `0.991x` and the C-cycle ratio was `0.934x`.

The private prevalidated-PCG apply experiment was not retained. It preserved hierarchy structure, iterations, and backward errors, but its solve-time geometric-mean ratio was `1.012x` and the 20,000-vertex ratio was `1.008x`.

The direct terminal-factor assembly experiment removed one dense setup buffer while preserving the complete numerical test suite. Its geometric terminal-build ratio was `0.922x` and its worst case was `0.932x`; the candidate was retained.

The in-place canonical-edge compaction experiment preserved every graph and hierarchy benchmark invariant. Its graph-build timing geometric mean was `0.921x`, its hierarchy-build geometric mean was `1.034x`, and the best duplicate-heavy peak-RSS ratio was `0.949x`; the candidate was not retained.

### Terminal and workspace memory

- Completed strict-lower factors select packed-triangular or sparse row/column traversal storage by retained-byte cost.
- Sparse terminal indices use `u32` when valid.
- CMG matvec and residual roles share one full vector per hierarchy level.
- Recursive hierarchy levels write their solution directly into caller-provided output storage; the former per-level solution vector and final copy were removed.
- PCG fresh/final residual certification reuses existing storage; per-RHS PCG workspace was reduced from eight to six fine-dimension vectors at commit `f145ac92`.
- `GroundedLdl`, `CmgWorkspace`, and `PcgWorkspace` report retained principal heap bytes.
- Terminal LDL construction assembles the ordered grounded matrix directly from graph diagonals and active edges, eliminating one dense full-graph matrix and its permutation copy.
- Full public-quality `Components` metadata is retained only at the finest level. Coarse levels retain a minimal centering plan, use four-byte labels when labels are needed, and omit labels entirely for a single component.
- Coarse centering scratch stores only sums, compensation terms, and means; the larger projection workspace is retained only for the public finest-level compatibility boundary.

The in-place hierarchy-output experiment was retained at commit `40f7d234`. It reduced CMG workspace to about `0.707x` on paths and `0.691x` on worker–firm graphs, and complete PCG workspace to `0.860x` and `0.875x`. Same-run solve-time ratios were `0.992x` and `0.981x`, with unchanged hierarchy, iterations, residual certificates, and C parity.

For the original 20,000-vertex matched run, hierarchy diagnostics, iterations, and backward errors were unchanged. Relative to the frozen baseline, path CMG application and solve time fell substantially, principally because sparse terminal traversal avoided dense lower-triangle scans. Worker–firm workspace memory also declined materially.

### CSR and parallel execution

- `CsrLaplacian` stores deterministic canonical rows and uses compact four-byte neighbor indices when possible.
- Serial production solves retain the compact one-edge-per-undirected-edge scatter kernel because it is faster and smaller on measured cases.
- CSR is used for row-owned parallel matvec without atomics.
- Rayon is optional; `ParallelExecutor` owns an isolated custom pool with an explicit or detected thread count.
- Parallel batch solving shares the immutable hierarchy, uses private reusable workspaces, preserves input order, and caps concurrency using a workspace-memory budget.
- Deterministic parallel edge sorting, coarse contraction, and heavy-edge selection route only above conservative size/density floors.
- Forest splitting reuses traversal storage rather than allocating per leaf walk.

On a four-logical-CPU hosted runner with 50,000 vertices and eight RHSs, retained directional batch throughput is about 2.0x at two threads and 2.5–2.7x at four threads. Across-RHS parallelism remains the preferred route whenever several independent inverse actions are available.

## Pinned C differential qualification

The benchmark-only crate compiles standalone adaptations of the official C loops. It is never linked into the production library. Every timing comparison first verifies numerical agreement.

At 100,000 vertices on a hosted Ubuntu runner, the compact Rust serial edge matvec was faster than the pinned C sparse-symmetric matvec on both retained cases. Restriction and prolongation are numerically exact relative to C and close in speed; results depend on whether labels are contiguous or scattered.

The complete iterative stationary-cycle comparison exposed and repaired an important semantic discrepancy. Official CMG performs one top-level cycle and uses each level's repeat count for the child recursive solve. The Rust production path, both independent Rust references, tests, and documentation use that schedule as of commit `249c2d1f`.

After the compatible stationary-core optimization, same-run complete-cycle timing relative to pinned C was approximately:

| Case | Hierarchy levels | Quotient-space maximum scaled difference | Rust/C median time |
|---|---:|---:|---:|
| Path | 7 | `2.07e-12` | `1.30x` |
| Worker–firm | 8 | `8.88e-16` | `1.46x` |

After in-place level output and recursive centering, the accepted recursive-centering run measured Rust/C cycle ratios of about `0.866x` on paths and `1.008x` on worker–firm, with the same quotient-space agreement. These hosted-runner values are directional but show that the remaining cycle gap is now small and workload-dependent rather than a broad implementation deficit.

## Current hot spots

1. Planned PCG still performs dot products, Euclidean norms, Krylov vector
   updates, component centering, and residual reconstruction mostly serially.
2. Coarse contraction remains the dominant measured setup phase on
   worker–firm and dense worker–firm graphs.
3. Controlled 8-, 16-, and 32-thread/high-memory behavior is still unknown on
   ordinary hosted runners.
4. Further index compaction or duplicated sparse metadata must demonstrate an
   end-to-end memory benefit rather than only a local structure-size benefit.

## Rejected or deferred experiments

- Unsafe unchecked aggregation loops were rejected because the crate globally forbids unsafe production code; safe kernels were retained.
- Duplicating native and compact aggregation labels is not accepted without an end-to-end memory and speed win.
- Skipping public-compatible apply checks inside PCG was benchmarked and rejected because it did not produce a stable end-to-end solve improvement; the fully checked path remains.
- In-place canonical edge compaction was benchmarked and not retained because its timing or memory gates did not provide a stable end-to-end win.
- Pipelined CG, K-cycles, aggressive SIMD, NUMA pinning, and panel Krylov methods remain deferred until ordinary stationary CMG is allocation-free and profiles identify a remaining bottleneck.

## Checkpoint log

| Date | Commit | Result |
|---|---|---|
| 2026-08-22 | `cc9d641d` / `b45b252f` | Measurement infrastructure and frozen baseline established |
| 2026-08-22 | `703ddc2e` | Graph lineage and cached components qualified |
| 2026-08-22 | `a6b339fd` / `8022b568` | Persistent component scratch qualified |
| 2026-08-22 | `daa677b9` / `fd8db573` | Terminal compression and CMG scratch aliasing qualified |
| 2026-08-22 | `31f72e6a` / `365a3572` | CSR crossover measured; serial edge route retained |
| 2026-08-22 | `ffdc96de` / `3cff911e` | Optional custom Rayon pool and deterministic parallel batch solving added |
| 2026-08-22 | `636fa093` / `f17b377d` | Parallel benchmark reconstruction and cross-platform all-feature tests repaired |
| 2026-08-22 | `7e3ab0df` / `fdf5b1de` | Setup routing and forest traversal reuse qualified |
| 2026-08-22 | `31530bcf` / `090048bb` | Pinned C matvec comparison retained |
| 2026-08-22 | `32a49022` / `f536b336` | Pinned C restriction/prolongation comparison retained |
| 2026-08-22 | `f145ac92` | PCG workspace reduced from eight to six fine vectors |
| 2026-08-22 | `deadcb6c` | Immutable graph nonzeros and norm bound cached |
| 2026-08-22 | `249c2d1f` | Recursive repeat schedule aligned with official CMG; full C cycle differential passed |
| 2026-08-22 | `6d5f4cca` | Compatible stationary core retained; solve time improved with unchanged iterations |
| 2026-08-22 | `40f7d234` | Per-level solution scratch removed; memory and solve-time gates passed |
| 2026-08-22 | `f6378554` | Recursive full compatibility projection replaced by centering; C parity and solve gates passed |
| 2026-08-22 | compact centering metadata | Full component metadata retained only at the finest level; memory, solve, and C-cycle gates passed |
| 2026-08-22 | prevalidated PCG apply | Rejected: numerical results matched, but end-to-end solve timing did not improve |
| 2026-08-22 | direct terminal-factor assembly | Retained after full tests and same-host build timing |
| 2026-08-22 | in-place edge compaction | Rejected after graph/hierarchy timing and peak-RSS gates |

### Zero-copy forest-label checkpoint — 2026-08-22

- Moving `ForestGrouping.labels` directly into `Aggregation` was **not retained**. Geometric setup ratio: 1.022; geometric peak-RSS ratio: 1.006.
- Full formatting, Clippy, rustdoc, debug/release tests, and release build status:
  `success`.
- Machine-readable evidence:
  `.ci/performance/forest-label-move-latest.json`.

### Compact edge endpoint checkpoint — 2026-08-22

- Storing retained edge endpoints as checked `u32` values while preserving
  the public `usize` API was **retained**. Geometric time ratio: 0.989; geometric peak-RSS ratio: 0.893; best peak-RSS ratio: 0.751.
- The candidate uses a typed error for endpoints above `u32::MAX` and
  includes a permanent 16-byte `Edge` layout invariant when retained.
- Qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/compact-edge-endpoints-latest.json`.

### Compact graph-build buffer checkpoint — 2026-08-22

- Collecting validated edges directly into the retained 16-byte layout and
  compacting duplicate pairs in place was **not retained**. Graph time/RSS ratios: 0.872/0.869; hierarchy time/RSS ratios: 0.931/1.010.
- Endpoint ordering, weight ordering, compensated duplicate summation, and
  all graph/hierarchy invariants were required to remain unchanged.
- Qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/compact-edge-build-buffer-latest.json`.

### Hierarchy allocation probe checkpoint — 2026-08-22

- Added a benchmark-only global counting allocator that resets its peak
  after graph construction and reports additional live requested bytes during
  hierarchy construction, retained hierarchy bytes, and post-drop balance.
- The probe is isolated from production code and supplements, rather than
  replaces, process-level peak-RSS measurements.
- Formatting, benchmark Clippy, release compilation, and representative
  path/worker-firm/dense-worker-firm runs passed.
- Machine-readable evidence:
  `.ci/performance/hierarchy-allocation-probe.json`.

### Exact-allocation graph buffer checkpoint — 2026-08-22

- The compact in-place graph-construction buffer was requalified with the
  benchmark-only exact hierarchy allocation tracker and was **not retained**. Graph time/RSS: 0.868/0.861; hierarchy time/exact peak allocation: 0.947/1.013.
- Process peak RSS remains recorded as supporting evidence; the hard memory
  gate uses exact additional live requested bytes during hierarchy setup.
- Qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/compact-edge-build-buffer-exact-latest.json`.

### Trimmed compact graph-buffer checkpoint — 2026-08-22

- The compact in-place graph-construction buffer was amended to trim
  geometrically grown spare capacity after duplicate compaction and was
  **retained**. Graph time/RSS: 0.861/0.862; hierarchy time/exact peak/retained: 0.960/0.922/0.976.
- All numerical ordering and compensated summation rules remained unchanged.
- Qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/compact-edge-build-buffer-trim-latest.json`.

### Direct compact contraction checkpoint — 2026-08-23

- Building coarse levels directly in retained 16-byte `Edge` storage was
  **retained**. Geometric time/RSS ratios: 0.952/0.924; parallel time/RSS ratios: 0.942/0.883.
- Qualification status: `success`.
- Decision: full qualification passed; direct compact contraction met timing, memory, and parallel-signal gates.
- Machine-readable evidence:
  `.ci/performance/direct-compact-contraction-latest.json`.

### Parallel CMG application checkpoint — 2026-08-23

- The optional `ParallelCmgPlan` candidate was **not retained**. Four-thread geometric speedup: `1.231x`; minimum case speedup: `0.843x`.
- Full serial/all-feature formatting, Clippy, rustdoc, debug/release tests,
  release builds, and benchmark-crate qualification: `success`.
- Machine-readable evidence:
  `.ci/performance/parallel-cmg-plan-latest.json`.

### Selectively routed parallel CMG checkpoint — 2026-08-23

- The selectively routed `ParallelCmgPlan` candidate was **retained**. Four-thread geometric speedup: `1.279x`; minimum case speedup: `0.940x`.
- Full serial/all-feature formatting, Clippy, rustdoc, debug/release tests,
  release builds, and benchmark-crate qualification: `success`.
- Machine-readable evidence:
  `.ci/performance/parallel-cmg-routed-plan-latest.json`.

### Parallel PCG checkpoint — 2026-08-23

- The opt-in prebuilt-plan PCG candidate was **retained**. Full-solve four-thread geometric speedup: `1.237x`; minimum case speedup: `0.988x`.
- Validation status: `success`.
- Existing serial PCG remained unchanged throughout the experiment.
- Machine-readable evidence:
  `.ci/performance/parallel-pcg-latest.json`.

### PCG strategy-matrix checkpoint — 2026-08-23

- Benchmark-only strategy matrix status: `success` across 22 cases.
- Cases compare serial sequential, across-RHS parallel, and planned within-solve PCG.
- The provisional policy had geometric/worst auto-to-best ratios of
  `1.003x` and
  `1.079x`.
- Exhaustive observed-threshold search selected
  `150001` original edges for
  single-RHS planned execution, with geometric/worst ratios of
  `1.000x` and
  `1.000x`.
- No production routing was changed by this checkpoint.
- Machine-readable evidence:
  `.ci/performance/pcg-strategy-matrix.json`.

### Prepared parallel solver robust checkpoint — 2026-08-23

- The memory-aware prepared solver candidate was **retained** after robust,
  interleaved seven-sample timing.
- Validation status: `success`.
- Geometric/worst automatic-to-best explicit timing ratios:
  `0.989x` /
  `1.054x`.
- Geometric/worst automatic-to-selected explicit timing ratios:
  `0.986x` /
  `0.999x`.
- Machine-readable evidence:
  `.ci/performance/prepared-parallel-solver-robust-latest.json`.

### Packed endpoint-key checkpoint — 2026-08-23

- Replacing the two-endpoint comparator with one packed 64-bit endpoint key was
  **retained** after the exact-allocation recheck. Standard timing ratio: 0.970. Exact-gate geometric timing ratio: 0.981; maximum additional-peak/retained ratios: 1.000000/1.000000.
- Validation status: `success`.
- Decision: standard timing improvement was confirmed to have no exact requested-allocation regression.
- The earlier process-RSS-only comparison remains recorded as supporting noisy
  evidence; exact requested allocation is the controlling memory gate.
- Machine-readable evidence:
  `.ci/performance/packed-endpoint-key-latest.json` and
  `.ci/performance/packed-endpoint-key-exact-latest.json`.

### Contraction edge-survival profile — 2026-08-23

- This was a read-only benchmark; production numerical source was unchanged.
- First path level: `0.250` of edges survive,
  `12.0` MB of temporary compact-edge
  reservation is avoidable, and the median counting scan was
  `0.815` ms.
- First worker–firm level: `0.551` survive,
  `10.8` MB is avoidable, and the
  scan was `1.708` ms. The next
  level still has `4.8` MB of
  avoidable reservation.
- First dense worker–firm level: `0.942` survive, so an
  unconditional second pass would add work for only
  `1.5` MB of potential savings.
- The next candidate must be routed by edge count and contraction ratio;
  dense/high-survival levels keep the one-pass path.
- Machine-readable evidence:
  `.ci/performance/contraction-survival-profile.json`.

### Routed exact-capacity contraction checkpoint — 2026-08-23

- Reserving the exact number of surviving coarse-edge contributions on routed
  large, strongly contracting serial levels was **not retained**. Serial geometric time ratio: 1.011; exact additional-peak ratio: 0.977.
- Validation status: `success`.
- Decision: qualification passed, but timing, exact allocation, or parallel regression gates were not all met.
- Parallel mapping remained unchanged and was included as a regression gate.
- Machine-readable evidence:
  `.ci/performance/exact-capacity-contraction-latest.json`.

### One-shot automation cleanup — 2026-08-23

- Removed `17` completed self-removing experiment,
  qualification, formatting, and smoke-test workflows that had remained after
  concurrent CI commits.
- Removed `6` corresponding source-transformation scripts.
- All numerical decisions and benchmark evidence remain in Git history and in
  `.ci/performance/`; persistent workflows are now limited to ordinary Rust CI,
  serial performance, parallel performance, and pinned-C comparison.
- Production numerical source was unchanged.

### Hierarchy phase-profile checkpoint — 2026-08-23

    - Read-only benchmark status: `failure`.
    - Aggregate dominant measured phase: `unresolved`
      (0.0% of summed manual-profile time).
    - No completed cases.
    - The profiler reproduces and checks terminal reason, level vertex counts,
      matrix nonzeros, and fill accounting against production hierarchy
      construction before timing is interpreted.
    - No numerical production behavior was changed by this checkpoint.
    - Machine-readable evidence:
      `.ci/performance/hierarchy-phase-profile.json`.

### Hierarchy phase-profile rerun checkpoint — 2026-08-23

- Read-only benchmark status: `failure`.
- Aggregate dominant measured phase: `unresolved`
  (0.0% of summed manual-profile time).
- No completed cases.
- The profiler checks terminal reason, level vertex counts, matrix nonzeros,
  and fill accounting against production hierarchy construction before timing
  is interpreted.
- No production numerical behavior changed in this checkpoint.
- Machine-readable evidence:
  `.ci/performance/hierarchy-phase-profile.json`.

### Unstable compact-edge sort checkpoint — 2026-08-23

- Replacing stable comparison sorting with in-place unstable comparison sorting
  was **retained**. The comparator remains a total order on packed endpoint
  key and weight, so equal-comparator edges are numerically identical.
- Geometric hierarchy-time ratio: 0.907.
- Geometric exact additional-peak ratio:
  0.972.
- Worst per-case time ratio: 0.998.
- Full qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/unstable-edge-sort-latest.json`.

### Unstable compact-edge sort checkpoint — 2026-08-23

- Replacing stable comparison sorting with in-place unstable comparison sorting
  was **retained**. The comparator remains a total order on packed endpoint
  key and weight, so equal-comparator edges are numerically identical.
- Geometric hierarchy-time ratio: 0.907.
- Geometric exact additional-peak ratio:
  0.972.
- Worst per-case time ratio: 0.998.
- Full qualification status: `success`.
- Machine-readable evidence:
  `.ci/performance/unstable-edge-sort-latest.json`.

### Hierarchy phase-profile refresh — 2026-08-23

- The read-only profile completed successfully after the retained unstable sort.
- Coarse contraction remains the aggregate dominant setup phase at `72.4%` of
  attributed hierarchy time, including `65.1%` on worker–firm and `81.9%` on
  dense worker–firm. Path setup is instead dominated by forest splitting.
- Manual phase attribution reproduced production terminal reasons, level sizes,
  nonzero counts, and fill accounting before timing was interpreted.
- No numerical production behavior changed in this profile.
- Machine-readable evidence:
  `.ci/performance/hierarchy-phase-profile.json`.

### Routed in-place endpoint-radix checkpoint — 2026-08-23

- The safe in-place MSD endpoint-radix candidate was **not retained**.
- It is routed only for large serial compact coarse-edge buffers with at least
  four mapped contributions per coarse vertex; path-like levels retain the
  comparison sort, and the existing parallel sort is unchanged.
- Exact weight total ordering inside duplicate endpoint groups and compensated
  summation are preserved.
- Validation status: `not_run`.
- Geometric hierarchy-time ratio:
  `nan`.
- Worst per-case hierarchy-time ratio:
  `nan`.
- Geometric exact additional-peak ratio:
  `nan`.
- Machine-readable evidence:
  `.ci/performance/radix-endpoint-sort-latest.json`.

### Contraction subphase profile — 2026-08-23

- Read-only benchmark status: `failure`.
- Aggregate dominant contraction phase: `unresolved`
  (`0.0%` of attributed manual contraction time).
- No completed cases.
- Every reconstructed coarse graph was checked exactly against production
  endpoint order, weight bits, diagonal bits, matrix nonzeros, operator bound,
  and the production `Aggregation::contract` result before timing was used.
- No production numerical behavior changed in this checkpoint.
- Machine-readable evidence:
  `.ci/performance/contraction-subphase-profile.json`.

### Contraction subphase profile rerun — 2026-08-23

- Read-only benchmark status: `success`.
- Aggregate dominant contraction phase: `sorting`
  (`78.0%` of attributed manual contraction time).
- Completed cases: `3`.
- Every reconstructed coarse graph was checked exactly against production
  endpoint order, weight bits, diagonal bits, matrix nonzeros, operator
  bound, and `Aggregation::contract` output before timing was accepted.
- Production numerical behavior was unchanged.
- Machine-readable evidence:
  `.ci/performance/contraction-subphase-profile.json`.
### Two-stage endpoint/weight sort probe — 2026-08-23

- Benchmark-only candidate: **not promising** for a production qualification gate.
- Active worker–firm geometric sorting ratio: `nan`.
- Active worker–firm geometric manual-contraction ratio: `nan`.
- Worst per-case sorting ratio, including the path guard: `nan`.
- Exact coarse-graph equivalence passed inside every sampled level.
- No production numerical source was retained by this probe.
- Machine-readable evidence:
  `.ci/performance/two-stage-sort-probe.json`.

### Direct compact contraction and prepared parallel PCG checkpoint — 2026-08-23

- Direct construction of coarse graphs in retained 16-byte `Edge` storage was **retained** after full serial/all-feature qualification. Across six large serial and parallel cases, geometric hierarchy-build time was `0.952x` and geometric peak RSS was `0.924x`; the worst observed timing ratio was `0.974x`.
- The optional `ParallelCmgPlan`, plan-aware certified PCG path, and memory-aware `ParallelPcgSolver` are present in production source. The default dependency-free serial APIs remain unchanged.
- A four-thread stationary-application routing probe showed exact serial/parallel agreement and speedups from `0.96x` on sparse path-like work to `2.23x` on a dense worker–firm case. This confirms selective routing rather than unconditional parallelization.
- Machine-readable evidence: `.ci/performance/direct-compact-contraction-latest.json` and `.ci/performance/parallel-cmg-routing-probe.json`.
- Full certified PCG timing, not only stationary application timing, is the next numerical-performance gate.

### Full certified PCG canonical-edge routing checkpoint — 2026-08-23

- The default prepared single-RHS router uses planned execution from
  **350,000 canonical retained edges** when a parallel hierarchy operator
  exists and the executor has more than one thread.
- Tested routing-matrix SHA: `8749fa228e0b99f555a2a5e3838c1ac8843a9cea`; run `32649078880`.
- Status: `success`; numerical failures: `none`; routing
  mismatches: `none`.
- Maximum scaled serial/planned solution difference:
  `0.000e+00`.
- Geometric planned speedup across the matrix:
  `1.208x`.

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

- Machine-readable evidence:
  `.ci/performance/full-pcg-routing-latest.json`.

### Certified PCG phase profile — 2026-08-23

| Case | Iterations | CMG/matvec share | Serial outer share | Preconditioner | Dot products | Vector updates |
|---|---:|---:|---:|---:|---:|---:|
| path-150k | 29 | 47.8% | 49.7% | 42.9% | 7.0% | 2.6% |
| worker-firm-300k | 20 | 47.4% | 49.0% | 41.4% | 7.0% | 2.7% |
| dense-worker-firm-400k | 9 | 81.0% | 15.6% | 74.0% | 2.2% | 0.9% |

- The profiler verifies bitwise equality with the ordinary planned solver.
- Machine-readable evidence: `.ci/performance/pcg-phase-profile-latest.json`.

### Single-component centering checkpoint — 2026-08-23

- The scalar-label fast path was **retained**.
- Qualification status: `success`.
- Decision: full qualification passed and connected-graph solve time improved.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
| path-150k | 0.757x | 0.767x |
| worker-firm-300k | 0.783x | 0.795x |
| dense-worker-firm-400k | 0.958x | 0.925x |

- The candidate preserves the original vertex order and Neumaier operation order; all numerical and structural benchmark fields were required to match exactly.
- Machine-readable evidence: `.ci/performance/single-component-centering-latest.json`.

### Post-centering PCG profile — 2026-08-23

| Case | Centering | Norms | Dot products | Vector updates | Preconditioner |
|---|---:|---:|---:|---:|---:|
| path-150k | 15.5% | 14.6% | 9.7% | 2.3% | 49.0% |
| worker-firm-300k | 14.2% | 13.2% | 8.9% | 2.9% | 49.0% |
| dense-worker-firm-400k | 3.8% | 3.7% | 2.6% | 1.0% | 78.9% |

- Machine-readable evidence: `.ci/performance/pcg-phase-profile-post-centering.json`.

### Fused centering and norm-scale checkpoint — 2026-08-23

- The fused solution-centering/norm-scale pass was **not retained**.
- Qualification status: `success`.
- Decision: qualification passed but the full-solve timing gate was not met.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
| path-150k | 1.003x | 0.998x |
| worker-firm-300k | 0.995x | 1.000x |
| dense-worker-firm-400k | 0.998x | 1.033x |

- The candidate preserves the centering subtraction order and the norm maximum/squared-sum orders. Every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/fused-centering-norm-latest.json`.

### Parallel outer-PCG centering checkpoint — 2026-08-23

- Parallel mean subtraction in the planned solver was **not retained**.
- Qualification status: `success`.
- Decision: qualification passed but the planned full-solve timing gate was not met.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
| path-150k | 1.007x | 1.044x |
| worker-firm-300k | 1.008x | 1.017x |
| worker-firm-600k | 1.000x | 1.004x |
| dense-worker-firm-400k | 1.000x | 1.036x |

- Compensated component sums and means remain serial and unchanged. Only independent per-vertex subtraction is parallelized, and every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/parallel-outer-centering-latest.json`.

### Fused centering and preconditioned-dot checkpoint — 2026-08-23

- The fused centered `r^T z` pass was **not retained**.
- Qualification status: `success`.
- Decision: qualification passed but the full-solve timing gate was not met.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
| path-150k | 0.989x | 0.985x |
| worker-firm-300k | 1.000x | 0.996x |
| worker-firm-600k | 0.996x | 0.998x |
| dense-worker-firm-400k | 0.999x | 1.004x |

- Centering subtraction, product evaluation, and Neumaier accumulation retain the same vertex order as the former separate loops. Every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/fused-centering-dot-latest.json`.

### Exact parallel norm-scale checkpoint — 2026-08-23

- Parallel max-absolute passes for planned-PCG norms were **retained**.
- Qualification status: `success`.
- Decision: full qualification passed; parallel max-absolute norm passes improved planned solves with exact results.

| Case | Serial solve ratio | Planned solve ratio |
|---|---:|---:|
| path-150k | 0.983x | 0.955x |
| worker-firm-300k | 0.950x | 0.939x |
| worker-firm-600k | 1.011x | 0.969x |
| dense-worker-firm-400k | 0.984x | 1.021x |

- Only the order-independent maximum pass is parallel. The compensated squared-sum pass remains serial in its original order, and every non-timing benchmark field was required to match exactly.
- Machine-readable evidence: `.ci/performance/parallel-exact-norm-scale-latest.json`.

### Deterministic fixed-chunk dot checkpoint — 2026-08-23

- Fixed-chunk planned-PCG dot products were **retained**.
- Qualification status: `success`.
- Decision: full qualification passed; fixed deterministic chunk reductions improved planned solves.

| Case | Serial solve ratio | Planned solve ratio | Planned/serial scaled difference |
|---|---:|---:|---:|
| path-150k | 0.989x | 0.985x | 2.36e-10 |
| worker-firm-300k | 1.014x | 0.959x | 3.50e-16 |
| worker-firm-600k | 1.004x | 0.940x | 6.22e-16 |
| dense-worker-firm-400k | 0.998x | 0.957x | 0.00e+00 |

- Chunk boundaries and the binary combine tree are fixed by `reduction_chunk_size`, so results are invariant to thread scheduling and thread count. Iteration counts were required to remain unchanged and final solutions/residual certificates remain independently verified.
- Machine-readable evidence: `.ci/performance/fixed-chunk-dot-latest.json`.

### Production-reduction profiler sync — 2026-08-23

- Profiler synchronization was **not retained**.
- Validation: `not_run`.
- Decision: profiler synchronization failed: expected local helper missing from block: fn compensated_sum.
- Representative bitwise-parity cases completed: `none`.
- Machine-readable evidence: `.ci/performance/pcg-profiler-sync.json`.

### Forest-backed contraction capacity checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Geometric hierarchy-time ratio: `1.0066420586495628`.
- Geometric exact additional-peak ratio: `0.992011742241337`.
- Evidence: `.ci/performance/forest-capacity-hint-latest.json`.

### Corrected two-stage contraction sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `failure`.
- Worker-firm geometric hierarchy-time ratio: `n/a`.
- Worst hierarchy-time ratio: `n/a`.
- Evidence: `.ci/performance/two-stage-sort-v2-latest.json`.

### Function-scoped two-stage sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `failure`.
- Worker-firm geometric hierarchy-time ratio: `n/a`.
- Worst hierarchy-time ratio: `n/a`.
- Evidence: `.ci/performance/two-stage-sort-v3-latest.json`.

### One-pass compensated merge checkpoint — 2026-08-23

- Candidate was **retained**.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.9775259095049927`.
- Worst hierarchy-time ratio: `0.9835428271471965`.
- Evidence: `.ci/performance/one-pass-merge-latest.json`.

### Current-kernel contraction profile — 2026-08-23

- Exact production equivalence passed at every level.
- Mapping/sorting/merging/diagonal shares: `11.2%` / `74.8%` / `8.7%` / `4.9%`.
- Evidence: `.ci/performance/contraction-subphase-profile-v2.json`.

### Raw graph two-stage sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Overall/duplicate-heavy graph-build ratios: `1.00652839386318` / `0.9707700491430172`.
- Evidence: `.ci/performance/raw-two-stage-sort-latest.json`.

### Sample-routed raw graph sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `failure`.
- Overall/duplicate-heavy graph-build ratios: `n/a` / `n/a`.
- Evidence: `.ci/performance/routed-raw-sort-latest.json`.

### Routed compact-edge radix sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.909923414399904`.
- Worst hierarchy-time / exact additional-peak ratios: `1.0134175253014959` / `1.1128925174302333`.
- Evidence: `.ci/performance/radix-compact-sort-latest.json`.

### Density-routed compact-edge radix sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.9380082112784894`.
- Worst hierarchy-time / exact additional-peak ratios: `1.0359722535557985` / `1.1231767791030263`.
- Evidence: `.ci/performance/routed-radix-compact-sort-latest.json`.

### Moderate-density compact-edge radix sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Worker-firm geometric hierarchy-time ratio: `0.958719162116395`.
- Worst hierarchy-time / exact additional-peak ratios: `0.9966956961782049` / `1.0610772414320586`.
- Evidence: `.ci/performance/moderate-radix-compact-sort-latest.json`.

### Compact aggregation-label checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `failure`.
- Geometric retained / hierarchy-time ratios: `n/a` / `n/a`.
- Serial/planned PCG geometric ratios: `n/a` / `n/a`.
- Evidence: `.ci/performance/compact-aggregation-labels-latest.json`.

### Corrected compact aggregation-label checkpoint — 2026-08-23

- Candidate was **retained**.
- Validation: `success`.
- Geometric retained / hierarchy-time ratios: `0.956863044090649` / `0.9934997461358874`.
- Serial/planned PCG geometric ratios: `0.995387475894316` / `0.9878997999485909`.
- Evidence: `.ci/performance/compact-aggregation-labels-v2-latest.json`.

### Post-compaction routing requalification — 2026-08-23

- Status: `success`.
- Routing / metadata / numerical failures: `0` / `0` / `0`.
- Planned-case geometric speedup: `1.6262176840051474`.
- Evidence: `.ci/performance/post-compact-label-routing.json`.

### Contraction profile after compact labels — 2026-08-23

- Status: `success`.
- Dominant phase: `sorting` (`76.2%`).
- Evidence: `.ci/performance/contraction-subphase-post-labels.json`.

### Lazy aggregation-size checkpoint — 2026-08-23

- Candidate was **retained**.
- Validation: `success`.
- Geometric retained / hierarchy-time ratios: `0.953003038418093` / `0.9983684256046577`.
- Serial/planned PCG ratios: `0.9867773370546498` / `0.9951106963463812`.
- Evidence: `.ci/performance/lazy-aggregation-sizes-latest.json`.

### Post-lazy-size routing requalification — 2026-08-23

- Status: `success`.
- Routing / metadata / numerical failures: `0` / `0` / `0`.
- Planned-case geometric speedup: `1.312632282108527`.
- Evidence: `.ci/performance/post-lazy-size-routing.json`.

### Lean forest hierarchy checkpoint — 2026-08-23

- Candidate was **retained**.
- Validation: `success`.
- Geometric / best exact additional-peak ratios: `0.967716344563314` / `0.8769852824098516`.
- Geometric hierarchy-time ratio: `0.9549888147228235`.
- Evidence: `.ci/performance/lean-forest-hierarchy-latest.json`.

### Post-lean-forest routing requalification — 2026-08-23

- Status: `success`.
- Routing / metadata / numerical failures: `0` / `0` / `0`.
- Planned-case geometric speedup: `1.5911556967322051`.
- Evidence: `.ci/performance/post-lean-forest-routing.json`.

### Owned internal forest split checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Hierarchy time / geometric exact-peak ratios: `0.9942737661478971` / `1.0`.
- Evidence: `.ci/performance/owned-split-forest-latest.json`.

### Owned internal component-label checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Hierarchy time / geometric exact-peak ratios: `0.9971156999848793` / `1.0`.
- Evidence: `.ci/performance/owned-component-labels-latest.json`.

### Parallel endpoint-first compact sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Active parallel setup / worst RSS ratios: `0.9957837547352953` / `1.0016806860478462`.
- Evidence: `.ci/performance/parallel-endpoint-sort-latest.json`.

### Density-routed parallel endpoint-first sort checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Active parallel setup / worst RSS ratios: `0.9891906343831914` / `1.0042045435660512`.
- Evidence: `.ci/performance/dense-parallel-endpoint-sort-latest.json`.

### Fused diagonal nonzero-count checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Graph / hierarchy timing ratios: `0.9956967888302344` / `1.0123246411133622`.
- Evidence: `.ci/performance/fused-diagonal-nnz-latest.json`.

### Trusted internal forest validation checkpoint — 2026-08-23

- Candidate was **retained**.
- Validation: `success`.
- Hierarchy / planned-PCG geometric ratios: `0.980289811480986` / `0.984276942977803`.
- Evidence: `.ci/performance/trusted-forest-validation-latest.json`.

### Owned trusted forest split checkpoint — 2026-08-23

- Candidate was **not retained**.
- Validation: `success`.
- Hierarchy / planned-PCG geometric ratios: `1.0007042230394185` / `1.0022955841523074`.
- Evidence: `.ci/performance/owned-trusted-split-latest.json`.

### Radix endpoint-sort checkpoint — 2026-08-23

- Adaptive sorted-input/radix endpoint ordering was **not retained**.
- Validation: `success`.
- Geometric hierarchy-build ratio: `1.172x`.
- Worst per-case hierarchy-build ratio: `1.323x`.
- Worst peak-RSS ratio: `1.100x`.
- Decision: correctness passed, but timing or temporary-memory limits were not met.
- Evidence: `.ci/performance/radix-endpoint-sort-latest.json`.

### Sampled contraction-capacity checkpoint — 2026-08-23

- Deterministic sampled survivor capacity was **not retained**.
- Validation: `success`.
- Geometric hierarchy-build ratio: `0.999x`.
- Geometric exact additional-peak ratio: `1.000x`.
- Worst process peak-RSS ratio: `1.002x`.
- Decision: qualification passed, but timing or exact/process memory gates were not all met.
- Evidence: `.ci/performance/sampled-contraction-capacity-latest.json`.

### Parallel endpoint-first sort checkpoint — 2026-08-23

- Parallel endpoint-first compact-edge ordering was **not retained**.
- Validation: `success`.
- Active worker/dense geometric setup ratio: `0.990x`.
- Worst setup ratio: `1.026x`; worst peak-RSS ratio: `1.092x`.
- Decision: qualification passed, but parallel setup timing or memory gates were not met.
- Evidence: `.ci/performance/parallel-two-stage-sort-latest.json`.

### Fused merge-diagonal checkpoint — 2026-08-23

- Diagonal accumulation during canonical duplicate merging was **retained**.
- Validation: `success`.
- Geometric graph-build / hierarchy-build ratios: `0.982x` / `0.982x`.
- Worst peak-RSS ratio: `1.002x`.
- Decision: full qualification passed; one canonical edge pass was removed with stable end-to-end setup gains.
- Evidence: `.ci/performance/fused-merge-diagonal-latest.json`.

### Fused diagonal-statistics checkpoint — 2026-08-23

- Diagonal accumulation during canonical duplicate merging was **retained**.
- Validation: `success`.
- Geometric graph-build / hierarchy-build ratios: `0.981x` / `0.995x`.
- Worst peak-RSS ratio: `1.001x`.
- Decision: full qualification passed; one full diagonal-array pass was removed with stable end-to-end setup gains.
- Evidence: `.ci/performance/fused-diagonal-statistics-latest.json`.

## Current next action

1. Re-profile forest splitting/component labeling after the ownership decision.
2. Refresh cumulative retained optimization and memory guidance.
3. Run the manual 1–32 thread qualification on suitable hardware when available.
4. Defer further sort variants unless a materially larger stable opportunity appears.
