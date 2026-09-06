# Component-aware CMG: opt-in experiments

This opt-in study addresses [issue #2](https://github.com/johannes-schmieder/CMG/issues/2).
The branch starts from `main` at `90e1fe0b0c14065155532711246ede6678bb4935`.
Existing builders, SDDM normalization, automatic routing, and SCC campaigns keep
their existing behavior. This is a local development prototype, not a promoted
algorithm or a platform qualification.

The current development recommendation is compact coarse storage with
`preserve_unpruned_stopping: true`, plus ordered sparse terminal factors. The
latest kernel checkpoint below improves both connected terminal setup and
mixed-component cycles. The original active-count stopping experiment remains
available for comparison; it still regresses on some worker-firm cases.

## Current approach

Start with two independent changes that retain one global PCG recurrence:

1. Remove **exactly isolated coarse vertices after contraction**. Preserve every
   parent row and its pre/post smoothing. A collapsed component's coarse range
   has dimension zero; removing that coarse degree of freedom does not mean the
   original component has been solved exactly.
2. Factor direct terminals with ordered sparse updates, one component at a time,
   including connected terminals. Keep the same anchor and relative degree
   ordering within each block, and store the final factors in shared buffers.
   One-vertex grounded blocks use their scalar pivot without a dense allocation.
   No heavy solver or executor is constructed per component.

All experimental options are under `experimental-components` and are off by default:

```rust
use cmg::{CmgOptions, CmgPreconditioner, ComponentBuildOptions};
let preconditioner = CmgPreconditioner::build_component_experiment(
    graph,
    CmgOptions::default(),
    ComponentBuildOptions {
        prune_coarse_isolates: true,
        factor_terminal_components: true,
        preserve_unpruned_stopping: true,
    },
)?;
// Use the existing certified solve_pcg / caller-workspace / planned interfaces.
```

Keep baseline, pruning-only, block-LDL-only, and combined builds available. These
separate reduced vector work, changed hierarchy/terminal behavior, and dense
factorization work. A component portfolio with independent Krylov recurrences
is deferred until residual costs justify its larger ownership and scheduling
changes. A block-diagonal collection of fixed component preconditioners under
one global PCG remains another possible intermediate design.

## Why this addresses the observed pathology

The original direct threshold uses the complete level vertex count. A graph
with 1,001 connected components can never drop below the default threshold of
700 while retaining one representative per component. A material component
therefore continues coarsening, with tiny representatives scanned repeatedly.
Recursive repeats multiply these scans even when setup and PCG iteration counts
look ordinary.

For the 4,096-vertex path plus 1,000 independent pairs, the baseline dimensions
are `6096, 2024, 1256, 1064, 1016, 1004, 1001`. Active-count pruning gives
`6096, 1024, 256`. The baseline's nonterminal cycle-weighted vertex count is
502,592, of which 484,000 represent isolated vertices. The candidate count is
10,192 with no isolated vertices at nonterminal coarse levels. This is a work
model, not a count of all arithmetic instructions or all finest-level scans.

Block factorization addresses an independent threshold cliff. With 349 pairs
the baseline immediately factors a 349-dimensional grounded matrix; with 350
pairs it coarsens first and factors an empty grounded matrix. Per-component
factorization reduces dense arithmetic from `O((sum d_c)^3)` to
`O(sum d_c^3)`, with dense temporary storage bounded by the largest block.
It does not remove finest-level PCG operations.

## Transfer and numerical contract

`Aggregation` continues to describe a complete partition with valid coarse
indices for every fine vertex. A pruned level instead holds `PrunedTransfer`, a
flat ordered list of `(fine_vertex, active_coarse_vertex)` entries. It retains
both the original aggregate count and the active child dimension. The full
aggregation is released; there are no sentinel indices presented as ordinary
coarse vertices and no inactive child vectors.

`HierarchyLevel::aggregation()` is `None` on a pruned level; inspection code must
check `pruned_transfer()`. `graph()` and the existing vertex-count report always
describe the actual stored graph. The transfer exposes the pre-pruning coarse
count separately. The experimental benchmark handles both kinds; existing SCC
exporters have not been extended to accept this experimental representation.

If `E` selects nonisolated coarse vertices, the transfer is `R_a = E R`, its
prolongation is `R_a^T`, and the child graph is exactly `R_a L R_a^T`. Only whole
isolated coarse vertices are removed, using exact zero degree, never a weight
threshold. Within each surviving aggregate the summation order is unchanged.
The compact graph preserves vertex order and positive edge values.

The existing symmetric pre/post damped-Jacobi cycle, coarse component centering,
and fixed recursive repeat counts remain in use. Active-count pruning changes
which terminal is reached and can change repeat counts; equality to the old
preconditioner is not promised. No inner tolerance-stopped solve, RHS-dependent
route, timing probe, or flexible preconditioner is introduced. Positivity and
symmetry are on the component-compatible quotient space; intermediate grounded
corrections need not have zero mean. Final PCG centering and original-system
certification remain authoritative.

The block factor's exposed permutation is grouped by deterministic component
order. Its local degree order and highest-index anchors match the baseline.
The component builder now also uses ordered sparse factorization for connected
terminals; the ordinary builder retains its reference dense factorization.
New sparse factor buffers
discard spare capacity before retention. Pruned-transfer retained storage is
included in hierarchy reports; ordinary workspace sizing follows actual compact
child dimensions. The workspace budget still has its documented meaning: it
does not admit total process RSS or every setup allocation.

## Validation and experiment

`tests/component_experiment.rs` covers:

- connected/disabled hierarchy and application identity;
- expected retirement on the mixed regression fixture;
- transpose and Galerkin identities for partial transfers and dimension errors;
- range linearity, symmetry, positivity, and dirty-workspace reuse;
- empty/all-isolated graphs, empty coarse levels, and incompatible isolated RHSs;
- grounded reference agreement with interleaved components;
- independently certified known solutions and heterogeneous component weights;
- no correction leaking into an untouched component;
- prepared numerical generations, current-operator certification, and rejection
  of unrelated topology lineage;
- deterministic planned execution with 1/2/4 threads and a single-workspace
  concurrency budget.

`component-bench` supplies connected paths/grids, material-plus-pairs,
material-plus-isolates, two material components, all-pairs, all-triangles, and
both sides of the direct threshold. It rotates the selected arms and records raw
paired samples after two warm-up rounds. Total includes setup, PCG workspace
allocation, solves, normal certification, and result allocation. Extra external
verification is performed for every sample. The current harness adds a fifth
arm preserving the original stopping decisions. Structural work and retained
bytes are reported separately; requested-allocation counters use a separate
build. Process RSS and allocation-failure recovery remain unqualified.

See `benchmarks/README.md` for the exact command. Small local results are
development evidence, not production thresholds or cross-platform speed claims.

## Recorded local screen: September 6, 2026

Numerical and harness source: `5c42b911ca6a59a6467f96341263d873ea1c40c9`, clean
at build time. Compiler: Rust 1.97.1, LLVM 22.1.6; release profile, macOS aarch64,
serial execution. Both runs used the same binary, two warm-up rounds, nine
recorded paired rounds, ten fixtures, and all four arms. Four RHSs reuse one
preconditioner and one workspace sequentially; this is not a parallel scaling
comparison. There were 720 recorded samples and 1,800 successful original-system
certificates, plus independent recomputation for every result.

The following are ratios of median total times (baseline / combined candidate),
including setup and workspace allocation:

| Fixture | 1 RHS baseline / candidate, ms | 1 RHS speedup | 4 RHS speedup |
|---|---:|---:|---:|
| Connected path | 4.712 / 4.740 | 0.99x | 0.99x |
| Path + 1,000 pairs | 52.273 / 6.085 | 8.59x | 12.84x |
| Path + 1,000 isolates | 43.100 / 5.763 | 7.48x | 11.51x |
| Two paths + 1,000 pairs | 60.812 / 4.286 | 14.19x | 16.37x |
| 1,000 pairs | 0.106 / 0.089 | 1.19x | 1.14x |
| 349 pairs, below direct threshold | 6.431 / 0.035 | 185.94x | 72.31x |
| 350 pairs, at direct threshold | 0.037 / 0.033 | 1.15x | 1.13x |
| Connected grid | 4.085 / 4.082 | 1.00x | 1.00x |
| Grid + 1,000 pairs | 29.204 / 4.946 | 5.90x | 9.63x |
| 1,000 weighted triangles | 0.308 / 0.269 | 1.14x | 1.16x |

The separate arms show that pruning supplies the mixed-component gain, while
block LDL supplies the direct-threshold gain and improves the two-material-block
terminal. These results support continuing the narrow combined approach before
building a general component portfolio. They do not establish an automatic
route or a guarantee over arbitrary graphs. The connected path's median paired
candidate/baseline ratios were 1.007 and 1.011; individual noisy rounds reached
1.085 and 1.053. This screen does not establish a strict tail-regression bound.

Raw local evidence (not committed benchmark data):

- `/private/tmp/cmg-components-rhs1.jsonl`, SHA-256
  `e312896258763d60aad169e785d3555022de69a3bc598205023c699e12ed3540`.
- `/private/tmp/cmg-components-rhs4.jsonl`, SHA-256
  `035631251fcfe6d70aca7cb93757c40fb601ef6a4dd08496f707565ae5b439a5`.
- `benchmarks/target/release/component-bench`, SHA-256
  `bc2ca6c27fa8f909247888b30767ff923efd2d565501c5d310de813a08edd02d`.

Validation at this source: 91 default-feature tests and 129 all-feature tests
passed in both debug and release profiles, including ten component experiment
tests. Library and benchmark Clippy passed with warnings denied; formatting,
private rustdoc with warnings denied, all benchmark release targets, and a Rust
1.85.0 all-feature compatibility check passed. Linux/Windows, process-memory
qualification, fresh holdout graphs, and SCC execution have not been run.

## Remaining design work

- Pruning currently occurs after an accepted contraction. Finest-level isolates
  and ordinary pre-contraction stagnation/fill/maximum-level decisions remain.
  General component-local direct or stalled terminals are not implemented.
- Finest-level matvec, projection, vector updates, and global stopping still
  include all components. Sparse RHS skipping and per-component stopping are
  portfolio opportunities, not benefits of this prototype.
- Setup is serial. Existing planned application works, but partial-transfer
  prolongation currently uses a deterministic serial scatter. A single package
  executor still controls any parallel work.
- The local stress and separate-baseline screens below now cover weighted,
  bridge-heavy, dense, heterogeneous and threshold-adjacent material graphs,
  two fixed seeds, and multiple RHSs. Larger sizes, more independent graph
  families, platform qualification and the small connected-path slowdown still
  need investigation before setting policy. Do not start with a giant
  Cartesian SCC campaign.
- Assess setup allocation failure/peak accounting and downstream report/API
  consumers before proposing production integration. The generic component
  partition and component scheduler in issue #2 remain future work.
- Keep SDDM pre-augmentation partitioning separate. Mixed singular/nonsingular
  systems need a deliberate gauge contract: the existing global extraction can
  shift a singular block by the unrelated augmentation vertex. For
  `diag([[1,-1],[-1,1]],[2])` and RHS `[1,-1,4]`, current monolithic extraction
  gives `[1.5,0.5,2]`, whereas separately centered blocks give `[0.5,-0.5,2]`.
  Both have zero residual. Raw solution equality is not a valid acceptance gate
  without specifying the common normalization.
- Independent component PCG, if added, needs explicit absolute-tolerance
  allocation, global certification, iteration-budget semantics, and fail-closed
  error aggregation. Fixed component cycles under global PCG avoid that redesign
  in this checkpoint.

## Frozen local stress protocol

Before observing stress timings, freeze `component_fixtures::stress` at seed
`20260906`: shuffled weighted paths (weights from 0.001 to 1,000), 32 cliques
joined by weight-0.000001 bridges, sparse/dense connected bipartite graphs, and
eight heterogeneous material components. Each family has a counterpart with
997 independent pairs. Two material path cases straddle the global direct
threshold. Dimensions, weights and wiring remain unchanged if a solve fails.
The generated known solutions are centered within each connected component.

Run all four arms with one and four RHSs, preserving every failure and checking
each successful solve against the original operator. Record forward solution
error as well as residuals: weak bridges can admit large forward error despite
a small residual, so residual acceptance is not a claim of forward accuracy.
Use two warm-up rounds and nine recorded rounds. No production dispatch gate
or numerical tolerance changes are implied by the screen.

Compile a separate baseline from `main` at
`90e1fe0b0c14065155532711246ede6678bb4935` using the exact same harness files and
compiler. Alternate external process order, and retain the branch's baseline
arm as a control for common implementation changes. Compare graph structures,
iterations and residuals before interpreting timing differences.

Requested-allocation measurements use a separate `component-allocations` build.
Report setup peak/live requested bytes, workspace live bytes, library accounting
and conservative estimates. Require zero allocations in warmed caller-buffer
CMG application and PCG loops. These counters exclude allocator arena overhead,
RSS, preexisting inputs and transient internal storage used by a system realloc;
they do not qualify allocation-failure recovery or a process-memory limit.

## Storage pruning with unchanged stopping decisions

The frozen stress screen at `5084161` exposed a setup regression: active-count
pruning reaches dense direct terminals of 299 and 659 vertices in the two
worker-firm mixtures. The ordinary hierarchy continues contracting and uses an
iterative terminal. Median one-RHS setup increases from 0.26 to 3.90 ms and from
1.54 to 41.00 ms; total increases 3.05x and 4.17x. Four RHSs amortize some setup
but still regress 1.61x and 1.49x. Weighted paths and bridged cliques also regress
for one RHS, while improving for four. This rules out promoting the original
combined option as an unconditional improvement.

The next isolated experiment adds `preserve_unpruned_stopping: true` alongside
pruning. Keep a scalar count of retired coarse representatives and include it
in the original direct/full-contraction/stagnation vertex checks. Their graph
rows and recursive vectors remain absent. Isolates contribute zero matrix
nonzeros, so the original fill checks and repeat calculation already apply.
The zero-dimensional child terminates directly with a zero correction; it
cannot continue recursing on virtual representatives.

This route preserves the material hierarchy and stationary cycle of the
unpruned build up to floating-point rounding, with block LDL still available
for ordinary direct terminals. It introduces no fitted threshold, new inner
solver, RHS-dependent rule or production routing. Compare it with all four
existing arms on the unchanged sentinels and stress seed before deciding
whether earlier active-count termination deserves further development.

## Revised local comparison and holdout

Revised numerical source: `297a4a3beac086dfdf5b73707b6f60b45021cfab`. Compare
against separately compiled `main` at `90e1fe0b0c14065155532711246ede6678bb4935`,
with identical harness sources, fixture generators, compiler and release
settings. Both enable `parallel` and `profiling`; the branch additionally enables
the opt-in component feature. Execution is serial. Each process performs two
warm-up rounds and one recorded round for every selected fixture. Nine external
rounds rotate process order among `main`, branch baseline, and the revised
candidate. The one- and four-RHS runs each cover all 22 fixtures.

The table reports the median of nine paired `main / candidate` total-time ratios,
including setup, workspace allocation, solves and normal certificates. Ratios
above one indicate a speedup. The held-out wiring uses seed `20260907`, chosen
before observing its timings, with the same graph dimensions and weight rules.
It uses the same nine-round external comparison on all 12 stress fixtures.

| Fixture | Frozen seed, 1 RHS | Frozen seed, 4 RHS | Holdout, 1 RHS | Holdout, 4 RHS |
|---|---:|---:|---:|---:|
| Weighted path + pairs | 3.88x | 4.04x | 2.80x | 2.92x |
| Bridged cliques + pairs | 7.57x | 8.77x | 5.57x | 6.45x |
| Sparse worker-firm + pairs | 1.31x | 1.34x | 1.34x | 1.35x |
| Dense worker-firm + pairs | 1.93x | 2.14x | 1.09x | 1.10x |
| Heterogeneous material | 3.03x | 1.95x | 3.25x | 2.08x |
| Heterogeneous material + pairs | 1.12x | 1.16x | 1.16x | 1.19x |
| Original path + pairs | 8.82x | 9.14x | — | — |
| Original path + isolates | 8.17x | 8.37x | — | — |
| Original two paths + pairs | 9.65x | 9.78x | — | — |
| Original grid + pairs | 11.35x | 11.23x | — | — |
| 349 pairs, below threshold | 192.37x | 72.96x | — | — |
| Connected path control | 0.98x | 0.97x | — | — |
| Connected grid control | 1.00x | 1.00x | — | — |

All 162 external invocations succeeded. There were 1,836 recorded samples and
4,590 original-system certificates across the three arms, plus independently
recomputed residuals. Iteration counts, residuals, tolerances and known-solution
relative errors match `main` exactly in every recorded candidate and baseline
control sample. This supports the storage-only interpretation of pruning; it
is empirical agreement, not a universal bitwise guarantee for arbitrary inputs.

The connected path's paired candidate/main ratios are 1.023 and 1.029; the
branch baseline control is also slower at 1.028 and 1.047. Other connected stress
controls are near parity. These local process comparisons do not establish
confidence intervals or a tail-regression bound. The dense holdout's much
smaller gain also shows why graph-family endpoints cannot define a dispatch
threshold. No production policy has been selected.

Weakly connected systems still inherit the existing solver's forward-accuracy
limits: the frozen weighted mixture reaches a 5.11% relative solution error at
the default residual tolerance (2.76% in the held-out wiring). The candidate and
`main` agree exactly on those errors. Passing residual certificates is not a
promise of an equally small forward error on ill-conditioned systems.

### Requested allocation results

The separate instrumentation run covered all 22 revised fixtures with four RHSs.
Every warmed CMG application and caller-buffer PCG loop made zero allocations.
Every measured additional setup peak was below the conservative build estimate.
Selected requested setup peaks, in KiB, illustrate the early-factorization
problem and its removal:

| Fixture | Ordinary baseline | Active-count pruning + blocks | Preserve stopping + blocks |
|---|---:|---:|---:|
| Weighted path + pairs | 446.5 | 3,348.5 | 302.5 |
| Bridged cliques + pairs | 321.2 | 4,322.6 | 180.3 |
| Sparse worker-firm + pairs | 261.7 | 1,851.0 | 158.4 |
| Dense worker-firm + pairs | 1,098.0 | 8,769.8 | 896.1 |
| Original path + pairs | 391.6 | 1,230.9 | 281.0 |
| Original grid + pairs | 264.2 | 1,357.1 | 129.1 |

Additional live allocations exclude the already constructed input graph, whose
shared storage is included in the preconditioner's retained-byte report. The
workspace report counts principal arrays; allocation tracking also sees vector
headers and metadata (128–1,528 additional bytes in these cases). These are
different accounting boundaries, not exact RSS measurements. No allocator
failure injection or total-process budget qualification was performed.

### Reproduction and local evidence

The harness and fixtures are committed; raw local measurements stay outside
the repository. Each external directory contains invocation commands, order,
return codes, source and binary identities in `manifest.json`, plus JSONL and
stderr files. Its `SHA256SUMS` file hashes all those records:

- `/private/tmp/cmg-components-5084161-external/SHA256SUMS`:
  `998409e7f40c0e710c52aea3f328ee09b7aafa2cbe845dd50c84275714997f04`.
- `/private/tmp/cmg-components-297a4a3-external/SHA256SUMS`:
  `18270c5013647f4052839911d1f051c4a51932e64be6bbf7416042a16691729a`.
- `/private/tmp/cmg-components-297a4a3-holdout/SHA256SUMS`:
  `546aee84bc6a015261ab31691d808885fed6ed1ae4a6517ca0adcf032cd942d2`.

The initial four-arm stress files are
`/private/tmp/cmg-components-5084161-stress-rhs1.jsonl` (SHA-256
`5b460cee8fe75397177bf1f24db6fc9aabdf9dc75a21ea0b1c9c5c41c0c8057a`) and
`/private/tmp/cmg-components-5084161-stress-rhs4.jsonl` (SHA-256
`0d8455500a43339b68a3a71080c6c0b00e9fbafcc06e64f281377d5dfec5654e`).

Allocation files `/private/tmp/cmg-components-5084161-alloc-stress-rhs4.jsonl`
and `...-alloc-sentinels-rhs4.jsonl` have SHA-256 hashes
`dbed21fd6aa47171fb12a15a3fb11faa5770ad1aeed13d0af364ec06ef8cce15` and
`d1de78e3ce8b30cbdb918b3b684097d2560943213f27806a60b35dfb7f65ed6a`.
The corresponding `297a4a3` files have hashes
`a3345e15dddf25e0a51496ab52d79f017994e231416ab93ec3b1bd3bf345c36d` and
`c8b95a54006152df2cd5a7aa72442f0cfd7066e19b40fdbc2e27d62d45ef814d`.

The revised timing binary hash is
`fe7f76990c57d5f7c21ec86b98dd93579e052b14df508d632b8a887285e3006e`;
the separate main binary hash is
`54253dbbb9e2c13a3bbfb642a9c4ac2f8703bf895d4e92780566d6e56a31702f`;
the revised allocation binary hash is
`d914ecfcc33c1ab25abf2eacda05d6a71b9044878bbcf09f90b83c15889dcea0`.

Validation at revised source: 91 default-feature and 131 all-feature tests pass
in both debug and release, including 12 component tests. Regression coverage now
compares compact graphs, stopping reasons, repeats and full cycle applications
against the ordinary hierarchy, including fill/level guards and empty children.
Prepared-generation and planned 1/2/4-thread tests cover both pruning policies.
Root and benchmark Clippy with warnings denied, formatting, private rustdoc,
all benchmark release targets, fixture determinism/component-count tests, and
Rust 1.85.0 all-feature compatibility checks pass. Linux, Windows, SCC, larger
production workloads and automatic integration remain outside this checkpoint.

## Further kernel optimization protocol

The next local screen keeps the graph and solver contracts fixed and isolates
three implementation changes: a single transfer enum instead of two mutually
exclusive optional fields; monotone graph compaction that preserves existing
diagonals and shares unchanged canonical edges; and ordered sparse terminal
factorization behind the existing experimental component-factor option.

The new terminal kernel stores nonzero columns and links each row's entries in
increasing prior-column order. It applies the same left-looking products and
subtractions as the dense reference, skipping zero factor entries. It preserves
static degree ordering, component anchors, factor nonzero counts and final
packed/sparse solve storage. Nonpositive pivots and nonfinite factor entries
remain errors. No fill entry is dropped based on a numerical tolerance.

Exact factor/solve comparisons cover paths, intermediate and complete fill,
permuted vertices and scales from 1e-150 to 1e150. A roundoff-destroyed pivot must
still fail. Performance and allocation comparisons use separate committed
binaries and the frozen fixtures; old evidence remains intact. The sparse
kernel's linked-column scratch can cost more than dense scratch for sufficiently
filled factors, so requested peak memory is a separate acceptance measurement.

The additional `large` suite is frozen before measurement at seed `20260908`,
with ten cases up to 97,536 vertices. Compare one and four RHSs against the
previous recommended numerical source `297a4a3` using the same new harness.
Use a one-round pilot followed by three rotated external rounds if every pilot
solve certifies. This is a bounded local scaling check, not an SCC campaign.

The final loop changes separate sparse arithmetic arrays from row-link metadata,
advance packed triangular indices by addition, and use contiguous fine-vector
access for prefix transfers. Empty-child cycles retain both smoothing sweeps
but omit the unused middle residual, restriction and recursive correction in
both serial and planned execution. This is a structural zero-dimensional case,
not RHS-dependent skipping. Tests compare that cycle with two explicit Jacobi
sweeps and cover workspace reuse and 1/2/4-thread plans. The harness now records
noncryptographic fingerprints of full solution bits outside timings, in
addition to residuals and known-solution errors.

## Kernel checkpoint results

Numerical source: `0f2f9c54ff70765c680221b5290eff72bc912e55`. The final screen
uses Rust 1.97.1 release builds on macOS aarch64, serial execution, and identical
current harness sources for all arms. The 22 original fixtures use seed
`20260906`, one and four RHSs, and nine rotated external rounds against both
pinned `main` (`90e1fe0b`) and the previous recommended candidate (`297a4a3`).
The 12 held-out stress cases use seed `20260907` and nine rounds against the
previous candidate. The ten larger cases use the preselected seed `20260908`
and three rounds against that candidate. Each invocation still has two warm-up
rounds and one recorded round.

Selected median paired `main / candidate` total-time ratios follow. Total time
includes setup, workspace allocation, solves and normal certification; fixture
generation, additional residual recomputation and fingerprints are outside it.

| Fixture | 1 RHS | 4 RHS |
|---|---:|---:|
| Connected path | 2.07x | 1.30x |
| Connected grid | 2.18x | 1.40x |
| Weighted connected path | 8.31x | 3.19x |
| Bridged connected cliques | 17.69x | 7.07x |
| Sparse connected worker-firm | 2.59x | 1.60x |
| Dense connected worker-firm | 2.33x | 1.87x |
| Path + pairs | 10.04x | 10.17x |
| Path + isolates | 9.02x | 9.05x |
| Two paths + pairs | 10.68x | 10.68x |
| Grid + pairs | 12.94x | 12.80x |
| Weighted path + pairs | 3.99x | 4.24x |
| Bridged cliques + pairs | 7.82x | 9.09x |
| Sparse worker-firm + pairs | 1.32x | 1.34x |
| Dense worker-firm + pairs | 2.00x | 2.23x |
| Heterogeneous material | 4.39x | 2.29x |
| Heterogeneous material + pairs | 1.15x | 1.15x |

The previous connected-path total regression is removed principally by faster
setup, rather than evidence that every application cost has improved. Median
one-RHS setup falls from 2.46 to 0.14 ms for that path, from 12.94 to 0.33 ms
for the weighted connected path, and from 18.55 to 0.44 ms for bridged connected
cliques. The deliberately sparse material fixture just below the direct
threshold improves 297.66x/192.37x in total; this is a dense-factorization edge
case, not a representative workload gain.

Relative to `297a4a3`, original path/grid mixtures improve another 5–8%, while
most stress mixtures improve 0–4%. Held-out connected weighted paths improve
8.20x/3.10x, bridged cliques 17.16x/7.00x and sparse worker-firm graphs
3.29x/1.86x. Their held-out mixtures retain the prior gains with smaller
additional improvements of about 0–7%.

The larger cases show why setup gains should not be extrapolated to all graph
sizes. This table uses `297a4a3 / candidate`, not `main / candidate`:

| Larger fixture | 1 RHS | 4 RHS |
|---|---:|---:|
| Connected path | 1.009x | 0.989x |
| Path + pairs | 0.982x | 1.030x |
| Connected grid | 1.116x | 1.053x |
| Grid + pairs | 1.048x | 1.031x |
| Sparse connected worker-firm | 2.096x | 1.436x |
| Sparse worker-firm + pairs | 1.030x | 1.024x |
| Dense connected worker-firm | 1.004x | 1.004x |
| Dense worker-firm + pairs | 1.013x | 0.995x |
| Weighted path + pairs | 0.999x | 0.984x |
| All pairs | 1.015x | 1.032x |

Most larger cases have modest additional gains or approximately 1–2% timing
regressions. Three rounds do not establish confidence intervals or a universal
regression bound. Their remaining costs are predominantly outside the improved
small-terminal setup. Keep the feature opt-in; no automatic routing threshold,
per-component Krylov portfolio or production promotion is selected here.

An earlier all-route pilot with the same sparse terminal arithmetic still found
active-count stopping slower than preserved stopping for worker-firm mixtures:
2.61 versus 1.48 ms on the sparse case and 19.99 versus 5.87 ms on the dense
case, with one RHS. Active stopping helped other families. Faster factorization
therefore does not justify unconditionally changing the original stopping rule.

### Correctness and memory

All 156 final external invocations succeeded with empty stderr: 1,740 recorded
samples and 4,350 original-system certificates, plus fresh residual checks.
Every candidate's iterations, residuals, tolerances, known-solution errors and
full-vector bit fingerprints match every corresponding control sample. The
fingerprints are noncryptographic; these observations supplement exact unit
comparisons, not a proof of universal bitwise equivalence. The earlier forward
error limitations of the ill-conditioned weighted cases remain unchanged.

Separate four-RHS allocation runs covered all 32 fixtures. Every warmed CMG
application and caller-buffer PCG loop made zero allocations, and every measured
additional setup peak stayed within the conservative build estimate. Selected
requested peaks in KiB compare the previous recommended source to this one:

| Fixture | `297a4a3` | `0f2f9c5` |
|---|---:|---:|
| Connected path | 1,175.7 | 189.0 |
| Connected grid | 1,313.9 | 603.5 |
| Weighted connected path | 3,335.9 | 218.8 |
| Bridged connected cliques | 4,214.2 | 407.9 |
| Sparse connected worker-firm | 1,796.1 | 774.6 |
| Dense connected worker-firm | 8,675.1 | 7,849.3 |
| Material below direct threshold | 7,645.3 | 108.0 |

These retain the allocation-accounting boundaries described above; they are
neither RSS nor allocation-failure qualification. Dense fill still limits the
sparse factorizer's gains. No density-dependent factorizer dispatch is fitted.

Validation at this numerical source: 135 all-feature tests and 91 default-feature
tests pass in both debug and release, including 15 component tests with exact
sparse/dense comparisons and serial/planned empty-child cycles. Both benchmark
fixture tests pass. Root default/all-feature Clippy, benchmark all-feature
Clippy, formatting, private rustdoc with warnings denied, benchmark release
targets, and Rust 1.85.0 all-target/all-feature compatibility checks pass.
Linux, Windows, SCC, process-memory limits and production integration remain
unqualified by this local checkpoint.

### Frozen local evidence

The final directories contain manifests with commands, rotated order, return
codes, source identities and binary hashes. Their `SHA256SUMS` files cover the
manifests, JSONL and stderr records:

- `/private/tmp/cmg-components-0f2f9c5-final/SHA256SUMS`:
  `f46d6e888288214ec827d71f4ab39af1e9e8eb22e6756d03e42ed67e7de0a8c4`.
- `/private/tmp/cmg-components-0f2f9c5-large/SHA256SUMS`:
  `64b949067886e212492c7552bf5f1619be2517d2bf1b1a8e8b4a15edd4a3dcc0`.
- `/private/tmp/cmg-components-0f2f9c5-holdout/SHA256SUMS`:
  `f22339e714e032468b7fc519ec93a4b5b50c1eeb5394dcf0a3a389a80d5dfe5f`.

Allocation JSONL files under `/private/tmp/cmg-components-0f2f9c5-alloc-`:

- `sentinels.jsonl`: `3c93978682dc846a020eaf61178deae3c31a3c9b851689e25f7c05b8444bf7d7`.
- `stress.jsonl`: `48a9f527bf9589a640b7082ae64b88eb421d1ae9d89ca96fa31a4b9b796895f1`.
- `large.jsonl`: `a991a2f0efe62332a37ca26dd650c34cbf1a2d062f232af11b533d2407ac941f`.

The candidate timing binary SHA-256 is
`3715a9e98b9d29ad4f84c769a97d8c2885967f24407199c6d6464a9fe5de5040`;
the allocation binary SHA-256 is
`d4f9c243e46d00b31edfe3a1101e821e924d2c7fc161add9ce69437e47670600`.
Raw results and one-off comparison/audit scripts remain outside the repository.

## Follow-up investigation after merge

PR [#4](https://github.com/johannes-schmieder/CMG/pull/4) merged as
`1921d66f86455540336a73b76f7619deec0b75bb` after Ubuntu quality checks and
Linux/macOS/Windows tests passed. Its source commits remain in the merge history.
The follow-up branch starts at that exact merge and keeps the feature opt-in.

The `e0d21ae` harness adds a separate `--profile` mode using the existing PCG
phase profiler with one executor thread. Every profiled vector and diagnostic
must match scalar and planned PCG, followed by a fresh residual check. Nine
recorded rounds and four RHSs cover the 32 frozen cases and 12 held-out stress
wirings. Profiles allocate their own workspaces and add timers; use their phase
shares for attribution, not as an end-to-end speedup measurement.

Profiles identify finest-component centering as a substantial cost on mixed
paths: approximately 26% on the large path mixture, 33% on the large weighted
mixture, and 38% on the smaller weighted mixture. CMG application occupies
approximately 79% on the large connected grid and 88% on the large dense
connected worker-firm graph. These are separate optimization targets.

The first isolated prototype specializes finest-component centering when labels
are nondecreasing, so each component occupies one contiguous slice. It retains
the same compensated additions in vertex order, validates all inputs before
modifying any values, and subtracts one mean per slice. Constructors record one
feature-gated boolean; no new heap buffer is retained. Interleaved and single
components use the existing path. This experiment is compiled only with
`experimental-components`; within that build it also applies to ordinary
builders and prepared component metadata.

Compare the prototype to `e0d21ae` with the same compiler and harness, preserved
stopping, frozen seeds and identical RHSs. Start with one paired round across
all 32 cases. If correctness passes, use nine rotated external rounds for the
22 original cases and three for the ten larger cases, with one and four RHSs.
Check exact diagnostics and solution-bit fingerprints before interpreting
total times, and separately check warmed allocations and setup estimates.
The `20260907` stress wiring remains the held-out correctness/performance check.

### Investigation results and remaining priorities

The prototype numerical source is
`ce0b02410fd0b4fb293941f929457d54cb949255`; its reference is
`e0d21aed3f84268f46a27786051606e95a6dbfb5`, whose solver sources are identical
to merged `1921d66`. Both use the new harness and Rust 1.97.1 release builds on
the same local macOS aarch64 host. The table gives median paired
`reference / prototype` total-time ratios, including setup and certification:

| Fixture | 1 RHS | 4 RHS |
|---|---:|---:|
| Path + pairs | 1.231x | 1.256x |
| Path + isolates | 1.235x | 1.242x |
| Two paths + pairs | 1.178x | 1.190x |
| Grid + pairs | 1.120x | 1.132x |
| All triangles | 1.179x | 1.240x |
| Material at direct threshold | 1.348x | 1.382x |
| Large path + pairs | 1.170x | 1.210x |
| Large grid + pairs | 1.120x | 1.133x |
| Large all pairs | 1.037x | 1.079x |
| Large connected path control | 0.983x | 0.982x |

Contiguous mixed paths and grids improve consistently in the larger screen.
Interleaved worker-firm and weighted mixtures remain approximately at parity;
this specialization does not speed up arbitrary component layouts. The held-out
material-at-threshold case improves 1.339x/1.389x, while other held-out cases
mostly remain within approximately 1% of the reference. The large connected
path's approximately 2% paired regression is unresolved. Individual external
rounds also contain timing outliers, including regressions on some otherwise
improved small cases. These are median local results without confidence bounds,
not a universal no-regression qualification or a second merge recommendation.

The baseline profiling run contains 1,584 recorded profiles, all matching both
scalar and planned results bit for bit. Focused post-change profiles cover 108
additional solves on large path, grid and weighted mixtures. They support the
intended mechanism: median centering time on the large path mixture falls from
23.61 to 9.64 ms, and on the large grid mixture from 9.35 to 5.02 ms. Centering
shares fall from 26.1% to 12.5% and 22.3% to 13.5%, respectively. The interleaved
weighted mixture stays at 33.2%. These separate instrumented runs explain the
mechanism; the paired uninstrumented runs above establish the reported ratios.

All 120 final comparison invocations succeeded with empty stderr: 1,344 samples
and 3,360 original-system certificates. Iterations, residuals, tolerances,
known-solution errors and full-vector bit fingerprints match in every pair.
Allocation instrumentation passes on all 32 cases: zero warmed application and
caller-buffer PCG allocations, and requested setup peaks within the conservative
estimates. The two new centering tests cover exact fallback comparisons,
prepared metadata, signed zero, cancellation, wide scales, nonfinite errors,
unchanged input on failure, and workspace reuse. All 137 all-feature and 91
default-feature tests pass in debug and release; root/benchmark Clippy,
formatting, private rustdoc, fixture tests and Rust 1.85 compatibility checks
also pass. The new kernel remains on the investigation branch and has not yet
received its own cross-platform CI qualification.

The next priorities follow from the measured costs:

1. Investigate a retained traversal plan for interleaved components, preserving
   ascending vertex order inside each compensated sum. Compare the cost of
   extra indices and indirect reads with the existing labeled pass; account for
   retained memory and setup before adopting it. The weighted mixtures still
   spend roughly one third of solve time in finest-component centering.
2. Attribute CMG application internally before changing it. Grids and dense
   worker-firm cases spend 79–88% of solve time there; distinguish edge
   traversals, smoothing, transfers, coarse centering and terminal solves before
   selecting another loop fusion or storage change.
3. Treat sparse terminal reach lists as a targeted setup experiment. Connected
   path terminals have about 0.8% strict fill, but setup is already only about
   3.5%/0.9% of large-path total time with one/four RHSs. The large sparse
   worker-firm terminal has 53.4% fill. Avoid extrapolating the abundance of zero
   scan slots into an end-to-end gain or selecting a dense/sparse threshold
   from these endpoints alone.

The original merged checkpoint also passed its normal post-merge Rust CI,
serial/parallel performance workflows and pinned C-kernel comparison. Those
results apply to `1921d66`, not to this subsequent prototype.

### Follow-up local evidence

Source and binary identities, commands, process order, return codes and raw
records remain in the local manifests. SHA-256 hashes of their `SHA256SUMS`:

- `/private/tmp/cmg-components-e0d21ae-profile/SHA256SUMS`:
  `c04ca634250b71a2f0bac2adef48dc1c5a8ad83aa2e5ebf9e7ebf819a106959c`.
- `/private/tmp/cmg-centering-ce0b024-final/SHA256SUMS`:
  `b0ea0f69ffb87bb3a470a5ae2b91111c254532e3c68e2a27bacaa43de785a7dd`.
- `/private/tmp/cmg-centering-ce0b024-large/SHA256SUMS`:
  `570d816877fd7d2b6e454811c2c747f2a55e3ebf8da3f0cf7c416fc87e75997c`.
- `/private/tmp/cmg-centering-ce0b024-holdout/SHA256SUMS`:
  `0c1151260c5b328b54d5dd6b7dbb4119a7f8a106955664f712f7047b969c2fc0`.
- `/private/tmp/cmg-centering-ce0b024-instrumentation/SHA256SUMS`:
  `5cc7dfd580b6893ba3ccb3343581afc5474754ca8a48e384e1fc241a7c701b2c`.

Reference timing binary SHA-256:
`bcc55e1da5c950c372378a5e7b97c3540dbcff86e1bb1ca8d2a40f6d65a0f60d`.
Prototype timing binary SHA-256:
`3098a59c3c8255c0ac1ffa8d9a38c7d75811c95898f11c0ace963b3953f69db1`.
