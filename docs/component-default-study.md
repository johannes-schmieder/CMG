# Component CMG default study — September 6–7, 2026

Keep main opt-in. Both proposed stages are implemented on
[`perf/component-default-study`](https://github.com/johannes-schmieder/CMG/pull/6),
but neither passes every frozen local performance gate. B is the stronger
candidate: it qualifies 159 of 160 connected controls and substantially speeds
ordinary disconnected callers. One connected control remains inconclusive after
the maximum 21 rounds. The bounded screen also omits some costly large-case
comparisons. This evidence does not justify a global default promotion.

The prior optimization checkpoint was merged in
[PR #5](https://github.com/johannes-schmieder/CMG/pull/5). Main remains at
`90d06d58edf7de43e6e78855b1b14c4b6311b808`; its experiments remain opt-in.

| Role | Frozen source |
|---|---|
| Previous main | `1921d66f86455540336a73b76f7619deec0b75bb` |
| Merged checkpoint | `90d06d58edf7de43e6e78855b1b14c4b6311b808` |
| A: ordered kernels for ordinary builds | `063dfd543ad76e1167301def4d8a88aa082bf6e2` |
| B: A plus preserved-stopping compaction and component factors | `1d0396f805fb358106c592c6362506d3cb01517d` |

A exposes the retained centering, paired norm, PCG reduction, residual/restriction,
terminal-scaling and empty-child kernels to ordinary builds. It preserves
hierarchy and factor selection. B additionally retires coarse isolates while
retaining their counts in stopping decisions, and uses component terminal
factors in all ordinary CMG builders, including executor and profiled builders.
The standalone `GroundedLdl::factor` keeps its reference behavior. Cargo defaults
remain empty; the tiny-component portfolio and active-count stopping policy
remain explicit experiments. There is no new automatic dispatch policy.

The [matched caller harness](../benchmarks/callers/README.md) depends on CMG with
default features disabled. Separate minimal and parallel binaries use identical
driver, fixture and lockfile bytes across sources. Timed total is setup plus
workspace/output-buffer preparation plus certified solve. Planned setup includes
the executor and plan. Graph/RHS construction, independent residual verification,
solution fingerprints and repeated-apply probes are outside that total.
Allocation and prepared-driver checks are separate from qualifying timings.

The machine was an Apple M2 Ultra, 24 cores, 192 GiB, macOS 26.6.2, with Rust
1.97.1. Scheduling was uncontrolled, with no reserved or pinned core. Nine
external rounds rotate source order and each invocation has two warmups and one
recorded repetition. Ratios are medians of paired reference/candidate total
times; intervals use 10,000 paired bootstrap resamples with seed 20260909.
These are exploratory 95% intervals without adjustment for multiple comparisons.
No Linux/Windows performance qualification or SCC work was performed.

Sources and gates were frozen before fresh seed 20260915. The original matrix
was reduced for runtime after six complete configurations and four complete
large-case rounds: the legacy large mixed solves projected roughly a day of
computation. The incomplete fifth round is excluded in full and preserved.
All 46 configured cases remain in the serial screen at one RHS, but
`large-path-plus-pairs`, `large-grid-plus-pairs` and
`large-weighted-path-plus-pairs` are omitted at four/sixteen RHSs and from the
parallel performance screen. Remaining serial cells use five arms; completed
original cells retain seven. No numerical source, tolerance, seed or performance
margin was tuned after seeing qualification results. This is an amended bounded
screen, not completion of the original full matrix.

The configured cases comprise 32 original fixtures, 12 fresh-suite cases and two
Veneto graphs. Two fresh-suite threshold sentinels repeat fixed inputs, leaving
10 seed-dependent fresh cases and 44 distinct graph/RHS inputs at one RHS. The
tables below separate these groups. Owning and caller-buffer paths cover one,
four and sixteen RHSs subject to the omissions above. Parallel-feature serial
callers cover four RHSs. Planned one/four-worker callers cover four RHSs throughout
the bounded screen and sixteen RHSs on its seven large cases and two Veneto graphs.

The frozen connected gate requires the interval's lower bound to be at least
`1 / 1.02 = 0.980392`, ruling out a slowdown greater than 2%. A clear loss beyond
2% elsewhere also blocks recommendation, and a target workload must establish
at least a 5% gain. Only inconclusive connected controls receive one extension
from nine to 21 rounds. No additional repetitions follow an unresolved result.

| Candidate | Connected controls qualified | Inconclusive after extension | Clear losses beyond 2%, any case | Supported target gains ≥5% |
|---|---:|---:|---:|---:|
| A | 157 / 160 | 3 | 0 | 39 |
| B | 159 / 160 | 1 | 0 | 287 |

Counts refer to case/caller/RHS configurations, not independent graph families.
Every parallel connected control qualifies after its allowed extension. The
remaining inconclusive controls are serial:

| Candidate | Case / seed | Caller / RHSs | Speedup | 95% interval |
|---|---|---|---:|---:|
| A | bridged-connected-cliques / 20260906 | owning / 16 | 0.9928× | [0.9778, 0.9956] |
| A | sparse-connected-worker-firm / 20260906 | buffer / 1 | 0.9929× | [0.9800, 1.0022] |
| A | dense-connected-worker-firm / 20260915 | buffer / 16 | 0.9910× | [0.9747, 1.0021] |
| B | large-dense-connected-worker-firm / 20260908 | buffer / 1 | 0.9953× | [0.9753, 1.0144] |

B's remaining point estimate is about 0.5% slower, but its interval still permits
about a 2.5% slowdown. Absence of a clear loss does not establish the frozen
non-regression margin.

Selected ordinary caller-buffer results for B versus the merged checkpoint:

| Graph | RHSs | Total speedup | 95% interval |
|---|---:|---:|---:|
| Veneto complete | 4 | 7.523× | [7.398, 8.046] |
| Veneto largest connected | 4 | 1.058× | [1.049, 1.076] |
| Original path + pairs | 4 | 13.627× | [13.517, 14.115] |
| Original dense worker-firm + pairs | 4 | 2.209× | [2.197, 2.283] |
| Large connected path | 4 | 1.211× | [1.181, 1.226] |
| Large path + pairs | 1 | 98.584× | [96.402, 99.727] |

On Veneto complete, marginal median total time falls from 914.817 ms to
119.685 ms: setup 2.206 to 2.089 ms, buffer/workspace preparation 0.066 to
0.044 ms, and solve 912.545 to 117.565 ms. These marginal medians need not sum
exactly or yield the paired-median speedup. The largest-connected total moves
from 70.400 to 66.571 ms. This uses the public LeaveOutKSS teaching graph with
synthetic known-solution RHSs, not an end-to-end AKM or Stata application.
The complete graph has 41,108 vertices and 44,050 canonical edges; its largest
component has 34,862 vertices and 39,652 edges. Observation counts supply weights.
The input CSV SHA-256 is
`93e57a413a8cfccdcb043c5d793105a67b2dc9ebd27d5d3a4f1800abf89a2241`.
The evidence package does not contain the input CSV or print observation IDs.

Geometric means below summarize this selected fixture mix, not expected
production speedups. The original subset contains extreme direct-factor boundary
cases: `material-below-threshold` reaches 316.6× at one RHS. Those effects must
not be generalized to connected workloads. Fresh10 excludes the two repeated
threshold sentinels.

| Serial buffer subset | Cases at 1 / 4 / 16 RHSs | A at 1 / 4 / 16 RHSs | B at 1 / 4 / 16 RHSs |
|---|---:|---:|---:|
| Original fixtures | 32 / 29 / 29 | 1.033 / 1.050 / 1.060× | 5.100 / 3.243 / 2.670× |
| Fresh10 | 10 / 10 / 10 | 1.004 / 1.009 / 1.013× | 3.513 / 2.617 / 2.007× |

At four RHSs with four planned workers, original29/fresh10 geometric means are
1.036×/1.011× for A and 3.196×/2.779× for B, each against its matching checkpoint
caller. The serial owning results and every case interval are retained in the
full comparison table.

Direct cumulative comparisons use previous main in the same harness. At four
RHSs with serial buffers, original29/fresh10 means are 1.049×/1.010× for A and
3.235×/2.600× for B. No ratios from different campaigns are multiplied.
B versus the checkpoint's already enabled preserved-stopping route is close to
parity: original29/fresh10 means 0.997×/0.987× at four RHSs. The large gains above
measure ordinary callers gaining access to existing component optimizations;
they are not additional gains over that opt-in route.

All 21,084 recorded timing samples, comprising 130,236 RHS residual certificates
across 5,712 invocations, pass the numerical audit. There are 12,372 expected
cross-source numerical/hierarchy comparisons with no mismatches. A matches the
checkpoint default; B matches its explicit preserved-stopping route. Complete
vector FNV-64 fingerprints and diagnostic bit patterns agree, including across
396 owning/buffer and 258 minimal/parallel-feature serial comparisons. These
noncryptographic fingerprints summarize full vectors; source tests also compare
outputs directly. All 156 measured planned one/four-worker comparisons agree in
their numerical fields, without extending that observation to other schedules.

Residual certification does not guarantee small forward error. The largest
known-solution relative error is **56.5973%** on
`large-weighted-path-plus-pairs` at one RHS, identically on previous main,
checkpoint, its preserved-stopping route, A and B. Its 65,533-vertex path has
weights ranging from 0.001 to 1,000. All versions return the same fingerprint,
20 iterations and one restart, with residual 0.00732723 below tolerance
0.00846786. The stopping rule bounds the residual using RHS norm, operator bound
and solution norm; it does not bound solution error on an ill-conditioned graph.
The largest residual/tolerance ratio across recorded configurations is 0.998122.
No tolerance was weakened. An accuracy-policy study should investigate this
existing limitation separately before presenting default solves as accurate
coefficient estimates for such inputs.

Separate allocation checks cover 46 cases at four RHSs for checkpoint, A and B.
All 138 records show zero warmed apply and caller-buffer PCG allocations, and
every measured setup peak is below its conservative estimate. These are requested
allocation bytes and retained-capacity estimates, not resident memory readings.

| Maximum across the 46 cases | Checkpoint / A | B |
|---|---:|---:|
| Setup peak additional bytes | 20,116,844 | 17,610,220 |
| Reported retained preconditioner bytes | 23,406,224 | 20,615,132 |
| Reported PCG workspace bytes | 14,940,128 | 9,600,000 |
| Largest setup peak / conservative bound | 80.12% | 26.15% |

On Veneto complete, retained preconditioner/workspace bytes change from
2,269,296/3,266,760 to 2,154,836/2,905,368. Memory does not shrink on every graph:
`material-at-threshold` retains 50,264 rather than 48,228 preconditioner bytes
while substantially reducing setup allocations. The records preserve such costs.

The fixed-topology driver passes all 16 source/case/worker configurations:
112 changing-weight frames, 336 required fresh-route diagnostics, and zero
required or optional retained/warm-route failures. Profile/production comparisons
and zero caller-buffer allocations pass. Automatic prepared callers pass all
24 configurations across four sources, three graph families and one/four workers.
Their explicit/automatic numerical assertions remain unchanged.
All reported explicit/automatic solution differences are zero in these cases.

The initial automatic-driver run retained 12 failures: an old assertion expected
across-RHS routing with one worker. Every reference and candidate correctly chose
serial, after passing the numerical assertions. Commit `9cb9d40` corrects only
that expectation. A separately hashed driver was built against each unchanged
frozen numerical source, and only the 12 failed cases were rerun in a new
directory. The original failed audit and logs remain immutable; the accepted
caller audit explicitly links them and the correction. These instrumented and
functional runs supply no qualifying performance ratios.

Compatibility work covers hierarchy inspection as well as solves.
`PrunedTransfer` and its read-only accessor are available without a feature;
`aggregation()` may be absent on a nonterminal level, so callers must use
`is_terminal()`. Terminal permutations group components while preserving order
within each component. Teaching CSV export retains surviving partial-transfer
rows. Component diagnostics report represented coarse counts. Memory reporters
include partial transfers without materializing lazy native labels. The
contraction profiler explicitly marks skipped partial-transfer levels and
incomplete totals. The pinned C comparison rejects unsupported partial transfers
before constructing unsafe C descriptors. No SCC launcher or evidence is changed.

A passed 99 default and 155 all-feature tests; B passes 103 default and 159
all-feature tests, with debug/release, minimal/parallel, formatting, Clippy,
private documentation and Rust 1.85 checks. Exact A/B source CI passed on
Ubuntu, macOS and Windows. Follow-up consumer checks pass locally, including
the teaching export regression and all four C comparison tests in debug/release.
PR CI now includes the transfer boundaries and one/four-worker automatic caller
smokes. The final evidence receipt records the final branch SHA and its CI run.

The [machine-readable summary](../.ci/performance/component-default-study.json)
records source identities, gates and audit hashes. Full raw evidence, both frozen
and amended plans, the interrupted original run, source archives, binaries,
auditors, correction provenance, validation logs and all 2,684 comparisons are
retained under `/private/tmp/cmg-default-study-evidence`. The local report copy is
`/private/tmp/cmg-component-default-study-report.md`; the package is
`/private/tmp/cmg-component-default-study-evidence.tar.gz`, with an external
`cmg-component-default-study-evidence-receipt.json` containing its verified
SHA-256 and final CI identity. Build caches and unpacked source duplicates are
excluded; exact source archives are retained.

Pause default promotion. A future qualification should start with an independent
measurement of B's remaining dense connected control, complete the omitted
comparisons, and test representative non-Apple hardware under a frozen protocol.
The weighted-path accuracy limitation deserves separate attention before further
micro-optimization. Existing accepted timing evidence must not be retuned or
repeated until it yields a preferred answer.
