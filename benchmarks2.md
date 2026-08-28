# SCC CMG diagnostics campaign (`benchmarks2`)

## Status and ownership

- **Owner:** Codex, working directly on `main`.
- **Plan created against:** `main` at `71d8d42d3cd850f8272a7a07e797c59c39334c42`.
- **First SCC study:** accepted 180-row single-RHS matrix plus six standalone C-kernel records; the four `batch16` tasks were still pending in the committed snapshot.
- **Primary output of this campaign:** an auditable diagnostics packet that lets GPT Pro identify the actual bottlenecks and propose a second, evidence-based optimization pass.
- **Production optimization during this campaign:** prohibited, except for narrowly scoped fixes required to make the diagnostic instrumentation correct. Do not mix performance changes with the diagnostic baseline.
- **Recovered first-study runs:** main `20260824T152234Z-a28f802-8111305`; accepted `batch16` supplement `20260824T201216Z-a28f802-148558d` (48/48 expected rows).
- **Diagnostic implementation status (2026-08-24):** SCC protocol, frozen matrices, production-path Rust timing hooks, MATLAB diagnostics, staged-memory drivers, counter/capability wrappers, validators, legacy extraction, deterministic reduction, and report scaffolding implemented. The exact production commit and new run IDs will be recorded immediately after the clean release-gate commit.
- **Preserved failed smoke:** `20260825T034001Z-16a087e9329a-b2v1-smoke`, SGE job `7305473`, both tasks `failed=0` but `exit_status=1`. The MATLAB wrapper exposed the upstream root but not its compiled `mex/` subdirectory, so `mx_splitforest_` was unresolved. No results from this run are accepted; the wrapper-only fix and rerun use a new commit and run ID.
- **Accepted post-diagnostic optimization qualification (2026-08-27):** source `761a0f022f20d1114d9f20589b60563eab6fcb84`, run `20260827T064306Z-761a0f022f20-b2v1-routing-opt2`, SGE array `7330295.1-5:1`, and archived clean watch `20260827T064807Z-cmg-optimization-routing-qualification-b9e348`. All 5 tasks and 55 configurations passed accounting and application validation on `scc-ef1`. Across the 15 forced-planned 8/16/32-thread cases, optimized/baseline geometric ratios were 0.701x for PCG and 0.747x for setup plus solve; serial controls were 0.980x and 0.975x. Hierarchy, iteration, routed-operator, plan-byte, and workspace-byte identities were unchanged. This later user-authorized optimization is separate from the frozen diagnostic baseline.
- **Accepted current Rust/MATLAB qualification (2026-08-28):** source `6fe9be77084a60cca330760361dd4c7addc77ccf`, run `20260828T021628Z-6fe9be77084a-b2v1-rust-matlab-current`, and SGE array `7341600.1-5:1`. All 5 tasks and 40 configurations passed accounting, application, identity, timing, and numerical validation on Gold-6242 hosts `scc-ff4` and `scc-fc4`. At 16 CPUs, geometric-mean current Rust/MATLAB ratios across the five one-million-vertex families were 0.182x for hierarchy setup, 0.379x for one CMG application, 0.388x for PCG, 0.391x for setup plus solve, and 0.227x for process peak RSS. Sixteen CPUs gave the best aggregate Rust time; grid and weak community were individually best at 8, the other three families at 16, and all five regressed at 32. Moving from 16 to 32 increased geometric-mean Rust total time by 35%.

Update this file as a live plan. Check off completed items, record run IDs and commit SHAs, and push after every logical unit of work.

## Mission

The first SCC study established that Rust has a large hierarchy-setup advantage, usually lower setup-plus-one-solve latency, and much lower process peak RSS. It did **not** explain why high-core scaling is weak. The aggregate Rust PCG advantage is concentrated in the dense worker-firm family, while path, grid, and weak-community graphs are flat or slower at high core counts. In the one-million-vertex dense Rust case, the solve scales well but hierarchy setup and parallel-plan construction erase most of the benefit.

This campaign must determine, with direct evidence rather than elapsed-time speculation, how much of the behavior is caused by:

1. serial or poorly scaling hierarchy phases;
2. the one-time cost and memory footprint of `ParallelCmgPlan` construction;
3. insufficient work per parallel operator on sparse and coarse levels;
4. memory-bandwidth saturation;
5. synchronization and global-reduction costs;
6. Rayon task creation, stealing, and granularity overhead;
7. NUMA placement and first-touch behavior across two sockets and four NUMA nodes;
8. CPU-frequency changes as more cores become active;
9. MATLAB sparse or MEX paths that do not actually use the permitted thread count;
10. measurement noise, host effects, thermal drift, or execution-order effects;
11. native stopping-rule and iteration-count differences; and
12. fixed runtime overhead in process-level RSS comparisons.

The end product must make it possible for GPT Pro to answer four separate questions:

- What should be optimized for **single-RHS latency**?
- What should be optimized for **repeated-RHS throughput**?
- What setup and plan objects should be **reused or cached**?
- What changes are most likely to reduce **peak memory and NUMA traffic**?

## Non-negotiable experimental rules

1. **Use one clean source commit per accepted campaign.** Before deployment, `git status --porcelain` must be empty. Record the exact commit, source-archive SHA-256, and binary SHA-256. Do not benchmark a dirty tree.
2. **Fix the source-identity path before any production run.** Every compiled Rust and C result must contain the exact expected source identity. A smoke validator must reject `unknown`; there must be no second provenance repair.
3. **Keep raw runs immutable.** A failed, noisy, warned, timed-out, or memory-failed run remains in its original run directory. Any rerun gets a new UTC run ID.
4. **Do not silently exclude scientific failures or outliers.** Mark them and retain them. Exclude only protocol failures such as a wrong binary, wrong input hash, scheduler failure, incomplete output, or incorrect binding.
5. **Use the same canonical binary fixtures.** Rust and MATLAB diagnostics must read the existing `graph.bin`, `rhs.bin`, `truth.bin`, and `metadata.json`. Primary profiling results must not use independently regenerated formulas.
6. **Instrument the real production control path.** Existing manual profiling binaries are useful checks, but the primary phase timings must be collected from the actual hierarchy, plan, CMG, and PCG paths. Do not infer production bottlenecks from a duplicated reimplementation.
7. **Default builds remain primary.** Use the same default Rust release and MATLAB/MEX build policy as the first study. Native-CPU builds may be a separately labeled sensitivity only.
8. **Separate latency from resource efficiency.** Report wall time, process CPU time, average active CPUs, and CPU-slot seconds for every major point.
9. **Separate cold preparation, reused preparation, and steady-state solve.** Never combine these into one ambiguous number.
10. **Treat unavailable counters as unavailable.** Record `unsupported` with the probe output. Never write zero for a counter that could not be measured.
11. **Use at least five measured repetitions.** The main replication matrix uses seven measurements after two warmups. Preserve every sample.
12. **Balance execution order.** Use a deterministic Latin-square or balanced rotation over core counts, implementations, strategies, and placements. Record the ordinal position and timestamps.
13. **Do not optimize production code before GPT Pro reviews the completed packet.** Diagnostic-only feature gates and benchmark code are allowed. Any unavoidable production fix must be isolated, justified, tested, and cause affected measurements to be restarted under a new run ID.

## Known benchmark facts that the diagnostics must explain

At one million vertices:

| Family | Rust best observed total core count | Rust 32-core penalty versus best | Main interpretation to test |
|---|---:|---:|---|
| weighted path | 1 | about 10% | no useful parallel operator work or overhead dominates |
| 2D grid | 8 | about 25% | high-core planned execution regresses materially |
| worker-firm degree 3 | 16 | about 17% | moderate solve scaling, but 32 cores are too many |
| worker-firm degree 16 | 32 | 0% | solve kernel scales, preparation dominates total |
| weak community | 8 | about 17% | high-core planned execution regresses materially |

For dense worker-firm degree 16 at one million vertices:

- one CPU: hierarchy setup about 2.666 s, PCG about 5.869 s, total about 8.596 s;
- 32 CPUs: hierarchy setup about 3.921 s, plan construction about 1.831 s, PCG about 0.883 s, total about 6.735 s.

The diagnostics must explain why the parallel hierarchy is slower, why plan construction is so large, why dense PCG scales while sparse PCG does not, and whether one-thread or low-thread setup followed by a high-thread solve is superior.

## Required repository layout

Do not overwrite or reinterpret the validated first-study protocol. Add a separate second-study area:

```text
benchmarks2.md
benchmarks/scc2/
    README.md
    bootstrap.sh
    deploy.sh
    submit.sh
    run_task.sh
    collect_accounting.sh
    validate_task.py
    validate_run.py
    capabilities.py
    topology.py
    schemas/
    tasks/
    analysis/
benchmarks/src/bin/
    scc2-diagnostics.rs
    plan-phase-profile.rs
benchmarks/matlab/
    scc2_diagnostics.m
    scc2_diagnostics_from_env.m
benchmarks/report2/
    README.md
    METHODOLOGY.md
    FINDINGS.md
    GPTPRO_HANDOVER.md
    benchmarks2.tex
    data/
    figures/
    evidence/
    MANIFEST.sha256
output/pdf/benchmarks2.pdf
output/gptpro/benchmarks2-packet.zip
.ci/performance/scc-diagnostics-latest.json
```

The exact implementation may reuse existing modules, but keep the first report and first SCC scripts reproducible and unchanged unless a clear correctness bug must be fixed.

## Canonical data products

All committed data must be plain CSV, JSON, JSONL, Markdown, or text so GPT Pro can inspect it directly. At minimum create:

### `benchmarks/report2/data/results.csv`

One row per accepted configuration, containing medians and robust dispersion summaries.

Required identity fields:

- protocol version;
- run ID and task ID;
- source commit and source archive SHA-256;
- Rust/C/MEX binary SHA-256 where applicable;
- upstream MATLAB/C commit;
- environment ID;
- host, kernel, microcode, compiler, MATLAB release;
- graph family, vertices, canonical edges, matrix nonzeros, RHS count;
- implementation and execution strategy;
- hierarchy thread count, plan thread count, solve thread count;
- exact CPU list, socket list, NUMA-node list, placement mode, memory policy, and first-touch policy;
- tolerance and maximum iterations;
- warmup count and measured repetition count.

Required result fields:

- hierarchy setup, preconditioner finalization, plan construction, apply, PCG, total, input/assembly, and workspace-allocation times;
- process CPU time for each stage;
- average active CPUs for each stage;
- iterations, restarts, native status flags, independently recomputed residual, backward error, reference-solution error, and energy-norm error;
- hierarchy levels, per-level vertices/nonzeros/repeats, plan operator count, plan bytes, graph bytes, hierarchy bytes, terminal-factor bytes, and workspace bytes;
- peak RSS, peak PSS where available, page faults, and context switches;
- counter-derived IPC, effective GHz, cache-miss rates, bandwidth, and remote-memory fractions where supported;
- median, IQR, MAD, minimum, maximum, and bootstrap interval for each timing.

### `benchmarks/report2/data/samples.csv`

One row per raw repetition and stage. Include order position and timestamps. Never commit only medians.

### `benchmarks/report2/data/phases.csv`

One row per phase, level, repetition, and configuration. Required phase names are specified below.

### `benchmarks/report2/data/counters.csv`

One row per event group, event, stage, and repetition. Include `time_enabled`, `time_running`, scaling percentage, and an explicit support status.

### `benchmarks/report2/data/memory.csv`

Separate process baseline, input load, graph assembly, hierarchy, plan, workspace, and solve peaks. Include implementation-owned byte counts separately from process RSS.

### `benchmarks/report2/data/hierarchy.csv`

One row per hierarchy level and configuration, including vertices, edges/nonzeros, aggregation count, repeat count, terminal reason, terminal-factor nonzeros, and plan-operator eligibility.

### `benchmarks/report2/data/batch.csv`

One row per RHS-count, strategy, core-count, and repetition. Report total batch time, normalized time per RHS, throughput, workspace concurrency, memory budget, and peak RSS.

### `benchmarks/report2/data/warnings.csv`

Include every MATLAB hierarchy flag and Rust warning at every core count, not only the 32-core cross-section. Record the exact flag value and documented upstream meaning.

### `benchmarks/report2/evidence/`

Commit compact capability, topology, validation, accounting, and provenance evidence. Keep large raw logs and binary profiler traces out of the normal Git tree; publish those as an immutable GitHub release asset and record its SHA-256 and release tag in the committed evidence manifest.

## Phase 0: recover and fully expose existing evidence

### 0.1 Finish the pending `batch16` study

- [x] Check the four existing SCC batch tasks and collect `qacct`, logs, result JSON, and `/usr/bin/time -v` receipts.
- [x] If they completed correctly, validate the full expected 48-row matrix: four graph/size tasks × six application CPU counts × two implementations.
- [x] If a task failed for a transport, wrapper, or environment reason, preserve it and rerun only under a new immutable run ID. No such rerun was required.
- [x] If a task failed numerically, timed out, or exceeded memory, preserve that as a scientific result; do not silently replace it. No such failure occurred in the accepted run.
- [ ] Add accepted batch rows to a revised first report and also ingest them into the second-study data products.
- [x] Until this is complete, keep every repeated-RHS conclusion marked unknown. The accepted 48-row supplement now resolves the pending status; its interpretation will be updated with the new batch matrix.

### 0.2 Extract more information from the first raw main run

The first committed CSV contains medians but not all diagnostic information already present in the raw archive. Without rerunning anything, extract:

- [x] every setup, plan, apply, PCG, and total raw timing sample;
- [x] user CPU time, system CPU time, elapsed process wall time, major/minor faults, voluntary/involuntary context switches, and RSS from the raw time receipts;
- [x] exact run order and timestamps;
- [x] exact MATLAB hierarchy flag at every CPU count;
- [x] complete per-level hierarchy vertices, nonzeros, and repeat counts;
- [x] Rust plan operators, plan bytes, workspace bytes, and all retained byte diagnostics;
- [x] host assignment by task;
- [x] exact source, environment, input, and binary identities available in manifests and logs.

Write these to `benchmarks/report2/data/legacy-main-samples.csv` and `legacy-main-evidence.json`. Do not alter the original first-study files.

## Phase 1: implement production-path diagnostic instrumentation

### 1.1 Source and binary identity

- [x] Standardize the build-time variable used by the Rust and standalone C drivers.
- [x] Add an `identity` subcommand that prints source commit, source archive digest, protocol version, compiler, feature set, target, and binary SHA-256.
- [x] Make the smoke validator compare the compiled identity against the run manifest before any timed job is accepted.
- [x] Record the exact MEX compiler command, MEX binary hashes, linked libraries, MATLAB release/update, and upstream commit.
- [x] Add CI tests that fail if a benchmark binary built with an expected identity prints `unknown`.

### 1.2 Shared fixture reader

- [x] Make all primary diagnostic binaries consume the existing canonical binary input directory.
- [ ] Add `--input-dir`, `--output`, `--repetitions`, `--warmups`, `--threads`, `--strategy`, `--tolerance`, and placement metadata arguments.
- [x] Continue to support the old synthetic convenience arguments only for local development; do not use them in accepted SCC diagnostics.
- [x] Verify hashes and dimensions before timing.

### 1.3 Hierarchy and preconditioner phases

Instrument the actual production hierarchy path under a benchmark-only or `diagnostics` feature. The default library build must incur no timing or allocation overhead.

Required phase records, overall and per level:

1. graph clone/reference setup;
2. maximum-weight/heavy-edge forest selection;
3. forest splitting;
4. low-effective-degree correction;
5. forest component labeling;
6. aggregation construction;
7. coarse-edge endpoint mapping;
8. endpoint sorting;
9. duplicate-edge merging;
10. diagonal accumulation and graph finalization;
11. inverse-diagonal construction;
12. hierarchy bookkeeping and fill checks;
13. terminal LDL/factor construction;
14. finest-component and centering metadata;
15. repeat-count recalibration;
16. all remaining preconditioner finalization;
17. total production hierarchy and total complete preconditioner construction.

The existing `hierarchy-phase-profile` and `contraction-subphase-profile` can be reused as validation aids, but the primary accepted numbers must come from instrumentation around the production functions. Verify that profiling leaves the hierarchy, terminal factor, repeats, and numerical output exactly unchanged.

### 1.4 Parallel-plan phases

Add `plan-phase-profile.rs` or equivalent production hooks. Record, per hierarchy level:

1. eligibility decision and reason;
2. row-count construction;
3. prefix sum/row-pointer construction;
4. allocation and initialization;
5. edge scatter or row fill;
6. any sorting or ordering pass;
7. diagonal or auxiliary-vector construction;
8. first-touch operation;
9. final validation;
10. retained bytes and operator dimensions.

Measure:

- cold plan construction;
- second construction from the same hierarchy;
- reuse of an already built plan;
- plan construction with 1, 8, 16, and 32 worker threads;
- plan construction under current, interleaved, and parallel-first-touch memory policies.

### 1.5 PCG and CMG phases

Extend the existing production `pcg-phase-profile` approach to the shared SCC fixtures and all five graph families. Record:

1. outer-solver setup;
2. stationary preconditioner calls;
3. finest-level matvecs;
4. residual recomputations;
5. dot products/global reductions;
6. vector updates;
7. component centering/projection;
8. norm calculations;
9. restart logic;
10. final residual certification;
11. unattributed time;
12. call counts for every phase.

Measure three Rust strategies explicitly:

- forced serial;
- forced planned within-RHS execution;
- production automatic routing.

The auto result must report why the route was selected. For repeated RHSs also report forced across-RHS execution.

### 1.6 Stage CPU time and utilization

Wall time alone cannot identify serial execution. Around every major Rust and MATLAB stage, record process CPU time as well as wall time. Compute:

```text
average_active_cpus = process_cpu_seconds / wall_seconds
```

For Rust use a benchmark-only process CPU clock. For MATLAB record `cputime` around the stage and independently wrap the process with `perf stat` or an equivalent tool. Ensure calibrated apply and solve loops run long enough—preferably at least two seconds—for stable utilization and counter readings.

### 1.7 Memory stages

Add separate-process diagnostic modes so process high-water marks are attributable:

1. empty process/runtime baseline;
2. binary input loaded only;
3. graph or MATLAB sparse matrix assembled;
4. hierarchy/preconditioner built;
5. Rust parallel plan built;
6. one workspace allocated;
7. full workspace pool allocated;
8. one solve completed;
9. repeated-RHS solve completed.

For Rust also record implementation-owned byte counts. For MATLAB record `whos` sizes for visible arrays and cells, while making clear that these do not include all runtime or MEX allocations.

### 1.8 Numerical comparability

Add an independent, common diagnostic suite for both implementations:

- relative residual;
- backward error;
- gauge-centered componentwise reference error;
- gauge-invariant energy-norm error;
- iteration count and restarts;
- native convergence/status flags;
- hierarchy metadata and repeat counts.

The primary study remains native tolerance `1e-8`. Add a tolerance frontier at `1e-6`, `1e-8`, and `1e-10`. If feasible without changing the production solver, add a separately labeled fixed-iteration diagnostic. A fixed-iteration run is allowed to report nonconvergence, but it must return finite diagnostics and must never be mixed with accepted production solves.

### 1.9 MATLAB warning investigation

- [x] Locate the exact upstream code path that sets the nonzero dense hierarchy flag.
- [x] Document the flag value and meaning in `benchmarks/report2/evidence/matlab-hierarchy-flag.md` with exact source path and line references.
- [ ] Record it for every dense size and thread count.
- [ ] Run a separate untimed validation-enabled MATLAB construction if the upstream interface supports it.
- [ ] Keep the original native timing configuration unchanged.

## Phase 2: topology and counter capability smoke test

Before submitting production arrays, run a dedicated smoke job that produces `capabilities.json` and `topology.json`.

Probe and record:

- `lscpu -J` and `lscpu -e=CPU,NODE,SOCKET,CORE,ONLINE,MAXMHZ,MINMHZ`;
- `numactl --hardware`;
- `lstopo-no-graphics` or `hwloc-ls` when installed;
- kernel, microcode, transparent-huge-page policy, NUMA balancing policy, and CPU governor;
- exact scheduler cpuset and `Cpus_allowed_list`;
- availability and versions of `perf`, `numactl`, `numastat`, `pidstat`, Intel PCM, LIKWID, and any SCC-supported counter tool;
- permitted `perf_event_paranoid` and uncore access;
- whether counter event groups multiplex;
- whether memory-placement commands actually change `/proc/<pid>/numa_maps`;
- MATLAB and Rust process CPU utilization on one small known case;
- exact binary and input identities.

The smoke must include at least one sparse and one dense 100,000-vertex fixture, both implementations, 1 and 32 CPUs, and all proposed output validators.

Do not submit production arrays until:

- source identities are exact;
- bindings match requested CPU lists;
- all schemas validate;
- numerical results pass;
- unsupported counters are represented explicitly; and
- generated outputs can be reduced into the report tables without manual editing.

## Phase 3: required experiment matrix

Use whole Gold-6242 nodes, one hardware thread per physical core, the same default builds as the first study, two warmups, and balanced execution order. Keep array concurrency at two unless SCC policy requires otherwise. Run in scheduler `$TMPDIR`.

### A. High-quality one-million-vertex replication

This is the new statistical and utilization baseline.

| Dimension | Values |
|---|---|
| Families | path, grid, worker-firm d=3, worker-firm d=16, weak community |
| Vertices | 1,000,000 |
| Implementations | Rust, MATLAB |
| Application CPUs | 1, 8, 16, 32 |
| Warmups | 2 |
| Measurements | 7 |
| Solver | native tolerance `1e-8` |
| Placement | current whole-node linear binding, fully recorded |

Required outputs:

- raw wall and CPU times for every stage;
- all numerical diagnostics;
- all warnings and hierarchy metadata;
- basic counters and effective frequency;
- process memory and faults;
- run order and timestamps.

This matrix must confirm or reject the original best-core-count pattern with uncertainty intervals.

### B. Rust routing and production-phase matrix

For the same five one-million-vertex fixtures:

| Threads | Strategies |
|---:|---|
| 1 | serial and auto |
| 8 | serial, planned, auto |
| 16 | serial, planned, auto |
| 32 | serial, planned, auto |

Use five measured repetitions after two warmups. Capture production-path hierarchy, plan, CMG, and PCG phase timings. Record route eligibility, operator count, thresholds, and retained plan bytes.

This matrix determines whether high-core regressions come from the planned kernel itself, the router, or plan construction.

### C. Decoupled setup/plan/solve experiment

Run on worker-firm d=3 and d=16 at one million vertices.

1. Fix solve threads at 32 and build the hierarchy with 1, 8, 16, and 32 threads.
2. For the best setup-thread count from the same run, solve with 8, 16, and 32 threads.
3. Compare:
   - fresh hierarchy + fresh plan + solve;
   - reused hierarchy + fresh plan + solve;
   - reused hierarchy + reused plan + fresh workspace;
   - reused hierarchy + reused plan + reused workspace;
   - reused hierarchy + no plan/forced serial solve.
4. Use seven measured repetitions for the total workflows and at least five for instrumented phase runs.

Report plan break-even RHS count:

```text
break_even_rhs = plan_build_time / (serial_per_rhs_time - planned_per_rhs_time)
```

If planned execution is not faster, report `no finite break-even` rather than a negative value.

### D. NUMA, placement, and first-touch matrix

Generate CPU lists dynamically from `lscpu`; do not hard-code SCC CPU numbers.

Run worker-firm d=3 and d=16 at one million vertices under:

1. 8 cores compact within one NUMA node;
2. 16 cores compact within one socket;
3. 16 cores split 8+8 across sockets;
4. 32 cores with the current linear placement;
5. 32 cores spread across all NUMA nodes;
6. 32 cores with memory interleaved across all NUMA nodes;
7. 32 cores with current placement plus explicit parallel first touch.

For Rust, run all policies. For MATLAB, at minimum run the compact-versus-split 16-core comparison and the current 32-core configuration with utilization counters. Use seven measured repetitions.

Record:

- exact CPU and NUMA lists;
- process and per-thread utilization;
- local versus remote page placement before and after each stage;
- bandwidth where supported;
- CPU migrations;
- setup, plan, PCG, and total time;
- RSS and page faults.

### E. Memory decomposition

Run path, worker-firm d=3, and worker-firm d=16 at one million vertices, for 1, 16, and 32 CPUs, both implementations. Use the separate-process stage modes described above and five repetitions.

The report must distinguish:

- runtime/process baseline;
- incremental input and sparse representation;
- hierarchy/preconditioner storage;
- plan storage;
- one workspace and the complete workspace pool;
- transient peak during setup;
- transient peak during solve.

### F. Accuracy-versus-time frontier

Run path, worker-firm d=3, and worker-firm d=16 at one million vertices for:

- tolerance `1e-6`, `1e-8`, and `1e-10`;
- one CPU and the latency-optimal measured core count;
- both implementations;
- five repetitions after two warmups.

Plot time against independently computed backward error and energy-norm error. This determines whether iteration-count differences materially affect the cross-implementation conclusions.

### G. Repeated-RHS throughput

First incorporate the pending 16-RHS study. Then run the following only after the existing batch results have been inspected:

| Dimension | Values |
|---|---|
| Families | worker-firm d=3 and d=16 |
| Vertices | 300,000 and 1,000,000 |
| RHS counts | 1, 4, 16, 64 |
| CPUs | 1, 8, 16, 32 |
| Rust strategies | serial loop, planned within RHS, across RHS, automatic |
| MATLAB strategies | native sequential PCG; separately labeled outer-parallel variant only if stable and supported |
| Measurements | 5 after 2 warmups |

Report:

- total batch wall time;
- normalized seconds per RHS;
- RHS per second;
- CPU-slot seconds per RHS;
- setup and plan amortization;
- selected workspace concurrency and budget;
- peak RSS and implementation-owned workspace bytes;
- numerical checks for every RHS.

Do not call the Rust and MATLAB batch paths algorithmically identical. They are a native supported-workflow comparison unless an explicitly matched scheduling variant is added.

## Phase 4: conditional scale and matched-work experiments

These runs distinguish edge-count granularity from graph topology. Run them after the mandatory matrices if the counter and phase evidence still leaves the cause ambiguous.

Construct approximately eight-million-edge cases:

| Family | Suggested vertices | Approximate canonical edges |
|---|---:|---:|
| path | 8,000,001 | 8,000,000 |
| grid | 4,000,000 | about 8,000,000 |
| worker-firm d=3 | 5,333,334 | about 8,000,000 |
| worker-firm d=16 | 1,000,000 | about 8,000,000 |

Run Rust at 1, 8, 16, and 32 CPUs with five repetitions. Run MATLAB at least at 1 and 32 CPUs if memory and time permit. Also add a two-million-vertex dense worker-firm case, about 16 million edges, if the smoke estimate remains well within the 256 GiB node allocation and job time limit.

The point of this matrix is to test whether useful scaling follows edge work, degree/topology, hierarchy shape, or memory intensity.

## Hardware-counter protocol

Use separate counter passes from the primary latency pass so counter multiplexing does not perturb headline timing.

### Required basic event groups

Probe exact event names on the SCC kernel. Prefer machine-readable `perf stat -x, --no-big-num` output.

1. **Core and utilization:** `task-clock`, `cycles`, `instructions`, `ref-cycles` if available.
2. **Scheduler:** context switches, CPU migrations, page faults.
3. **Cache and branch:** cache references/misses, branches/branch misses.
4. **Memory:** per-socket read/write bandwidth through uncore IMC, Intel PCM, LIKWID, or the SCC-supported alternative.
5. **NUMA:** local/remote node traffic or `numastat -p` deltas and `/proc/<pid>/numa_maps` summaries.

For every counter record:

- raw value;
- unit;
- event name;
- `time_enabled` and `time_running`;
- scaling percentage;
- stage duration;
- exact command;
- support status and probe error if unavailable.

Avoid large multiplexed event sets. If `time_running / time_enabled < 0.90`, rerun with smaller groups and retain the original attempt as diagnostic evidence.

Derived metrics must include:

- average active CPUs;
- IPC;
- effective GHz from cycles and task-clock, with limitations documented;
- LLC misses per thousand instructions;
- bytes per edge or vertex where meaningful;
- memory bandwidth and fraction of platform peak where supported;
- remote-memory share;
- slot-seconds and energy per solve if energy counters are available.

## Statistical analysis and figures

Do not hide raw observations behind lines. Every major plot must show all repetitions or a clearly linked raw-data panel.

Required summaries:

1. median, IQR, MAD, min, max, and bootstrap 95% interval;
2. paired or blocked Rust/MATLAB ratios where runs share the same task/node/order block;
3. geometric mean, median row ratio, and win count;
4. all-case, dense-only, and exclude-dense sensitivity;
5. best observed core count with uncertainty; report a tie when intervals overlap materially;
6. speedup, parallel efficiency, and CPU-slot seconds;
7. stage and phase time shares;
8. plan amortization and break-even RHS count;
9. CPU utilization, effective frequency, bandwidth, cache misses, and remote-memory share versus core count;
10. memory decomposition and incremental RSS;
11. accuracy-versus-time frontier;
12. repeated-RHS throughput and memory budget;
13. host and execution-order sensitivity;
14. exact MATLAB warning sensitivity.

Required figures:

- one-million-vertex latency and slot-seconds versus CPU count;
- Rust route comparison: serial, planned, and auto;
- setup/plan/solve stacked decomposition;
- hierarchy phase shares by family and core count;
- plan construction by level and subphase;
- PCG phase shares and call counts;
- active CPUs, effective GHz, bandwidth, and cache misses versus cores;
- compact versus split-socket and first-touch comparisons;
- process baseline and incremental memory;
- matched-edge scaling;
- accuracy-time frontier;
- repeated-RHS throughput and break-even plot.

Do not attribute causality in `FINDINGS.md` unless the discriminating measurement supports it. Classify each explanation as **supported**, **consistent but unproven**, **contradicted**, or **not measurable on this system**.

## Validation and acceptance criteria

A result set is accepted only when all applicable checks pass:

### Identity and scheduler

- exact run, task, source, binary, environment, upstream, and input identities;
- clean source snapshot;
- scheduler `failed=0` and `exit_status=0`;
- exact CPU binding and memory policy recorded;
- complete expected point and repetition grid;
- no duplicate identities.

### Timing and counters

- finite, nonnegative raw timings;
- exact median and dispersion recomputation from samples;
- process CPU time and wall time both present;
- plan timing samples validated, unlike the first validator’s narrower primary timing set;
- counter support and multiplexing status explicit;
- no missing value represented as zero.

### Numerical

- native production runs satisfy the same independent acceptance thresholds as the first study;
- every RHS in a batch is checked;
- fixed-iteration diagnostics, if used, are clearly exempted from native convergence but retain finite error metrics;
- hierarchy and plan metadata are deterministic across repetitions and thread counts where production promises determinism;
- Rust profiled and unprofiled paths produce the same hierarchy and solution certificates.

### Noise policy

Do not reject a point merely because it is noisy. Flag a point when, for example, MAD/median exceeds 10%, max/min exceeds 1.5, or beginning/end anchor runs indicate substantial drift. A predeclared replication under a new run ID may be added, but both original and replication remain in the packet.

## SCC deployment and run protocol

Use the existing project root:

```text
/projectnb/welfgr/cmg-benchmarks
```

A typical clean deployment flow should be implemented as scripts, not copied manually:

```bash
git checkout main
git pull --ff-only
git status --porcelain   # must be empty
SOURCE_SHA=$(git rev-parse HEAD)
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${SOURCE_SHA:0:12}-b2v1"

bash benchmarks/scc2/deploy.sh "$RUN_ID" "$SOURCE_SHA"
bash /projectnb/welfgr/cmg-benchmarks/code-b2/$SOURCE_SHA/benchmarks/scc2/bootstrap.sh "$RUN_ID"
bash /projectnb/welfgr/cmg-benchmarks/code-b2/$SOURCE_SHA/benchmarks/scc2/submit.sh smoke "$RUN_ID"
```

After smoke acceptance, submit the required arrays. Standard jobs should request the whole Gold-6242 node:

```text
-P welfgr
-pe omp 32
-binding linear:32
-l cpu_type=Gold-6242
-l mem_per_core=8G
```

Use a four-hour limit for matched-edge and batch tasks if the smoke estimate warrants it. Standard diagnostic tasks may retain two hours. Keep concurrency at two. Perform process-level sub-binding with topology-derived CPU lists inside the whole-node allocation.

Every task must write to scheduler `$TMPDIR` first and atomically copy completed results and receipts into the immutable run directory.

## Upload and Git workflow

### Code and protocol commit

Before any production measurement:

- [ ] implement the complete harness, schemas, validators, and smoke tests;
- [ ] run formatting, Clippy with warnings denied, tests, release tests, and all benchmark-target builds;
- [ ] let the existing cross-platform CI finish successfully;
- [ ] commit and push the clean diagnostic code to `main`;
- [ ] record that exact commit as the only production source for the campaign.

Suggested commit sequence:

1. `bench: add SCC diagnostics protocol and schemas`
2. `bench: add production-path setup and plan profiling`
3. `bench: add PCG, CPU-time, NUMA, and memory diagnostics`
4. `bench: add diagnostics validators and report generator`

Push after each unit. Do not wait until all instrumentation exists before preserving progress.

### Collection and reduction

Keep collected SCC runs under an ignored local directory such as:

```text
benchmark-runs2/<RUN_ID>/
```

Then run:

```bash
python3 benchmarks/scc2/validate_run.py benchmark-runs2/<RUN_ID> ...
python3 benchmarks/scc2/analysis/reduce.py \
    --run benchmark-runs2/<RUN_ID> \
    --report-root benchmarks/report2 \
    --latest-json .ci/performance/scc-diagnostics-latest.json
bash benchmarks/report2/compile_report.sh
python3 benchmarks/scc2/analysis/make_gptpro_packet.py
python3 benchmarks/scc2/analysis/verify_generated.py
```

The reduction must be deterministic. Re-running it on the same accepted run must reproduce every committed CSV, JSON, figure source, LaTeX fragment, PDF source checksum, and packet manifest.

### Raw archive

Create an immutable archive containing the complete raw evidence:

```text
cmg-scc-diagnostics-<RUN_ID>.tar.zst
```

Include raw JSON, samples, logs, time receipts, counter outputs, topology probes, scheduler accounting, manifests, failed attempts, and validation receipts. Publish it as a GitHub release asset tagged:

```text
scc-diagnostics-<RUN_ID>
```

Commit the release asset SHA-256, size, and tag in `benchmarks/report2/evidence/raw-archive.json`. The ordinary Git tree must still contain all reduced plain-text data required for GPT Pro’s analysis.

### Results commit

Only after full validation:

```bash
git add \
  benchmarks/report2 \
  output/pdf/benchmarks2.pdf \
  output/gptpro/benchmarks2-packet.zip \
  .ci/performance/scc-diagnostics-latest.json \
  benchmarks2.md

git commit -m "perf: publish second SCC CMG diagnostics <RUN_ID>"
git push origin main
```

Do not push only a PDF. GPT Pro must have the raw reduced samples, phase data, counters, methodology, provenance, and report source.

## GPT Pro packet

`output/gptpro/benchmarks2-packet.zip` must be deterministic and contain at least:

- `benchmarks2.md`;
- `output/pdf/benchmarks2.pdf`;
- `benchmarks/report2/METHODOLOGY.md`;
- `benchmarks/report2/FINDINGS.md`;
- `benchmarks/report2/GPTPRO_HANDOVER.md`;
- `benchmarks/report2/benchmarks2.tex`;
- all files under `benchmarks/report2/data/`;
- compact evidence and manifests;
- `.ci/performance/scc-diagnostics-latest.json`;
- a top-level packet manifest with SHA-256 for every member.

The handover prompt must instruct GPT Pro to:

1. recompute all headline ratios from raw samples;
2. separate native-workflow comparisons from matched-work and matched-accuracy comparisons;
3. identify which scaling hypotheses are supported or falsified;
4. quantify the maximum benefit available from hierarchy, plan, PCG, routing, NUMA, batching, and memory changes;
5. propose an optimization sequence with predicted end-to-end impact and explicit falsification tests;
6. avoid recommending production changes that do not address a measured bottleneck;
7. distinguish single-RHS latency, repeated-RHS throughput, setup reuse, and memory;
8. specify a benchmark gate for accepting each proposed optimization.

## Decision rules for the later optimization pass

The completed diagnostics should lead to one or more of the following evidence-based actions:

| Finding | Likely optimization direction |
|---|---|
| One or two serial hierarchy phases dominate | parallelize only those phases, or cap setup threads and decouple setup from solve |
| Parallel setup loses mainly through frequency | build hierarchy with fewer cores; avoid parallelizing predominantly serial setup |
| Plan construction dominates and break-even RHS is high | lazy plan construction, caching, or omit plan for single/small batches |
| Plan subphase is allocation/first-touch bound | preallocate, parallel-touch, or use NUMA-local operator storage |
| Sparse operators have too little work | stronger per-level granularity thresholds and lower automatic core counts |
| Bandwidth saturates by 8 or 16 cores | cap within-RHS threads and exploit across-RHS concurrency |
| Remote memory is substantial | pin workers and use parallel-local first touch/workspace ownership |
| Reductions dominate at high cores | fuse reductions/vector passes or investigate numerically safe pipelined variants |
| Rayon task overhead dominates coarse levels | static partitions, larger chunks, fewer parallel regions, or serial coarse levels |
| MATLAB CPU utilization remains near one | describe MATLAB scaling as effectively serial; do not infer C-versus-Rust language effects |
| Matched-accuracy results erase a time advantage | qualify marketing and optimize convergence/stopping behavior before kernels |
| Across-RHS batch scaling is strong | prioritize prepared solvers, workspace pools, and batch scheduling |
| Process RSS advantage is mostly runtime baseline | market deployment footprint carefully and emphasize incremental memory separately |

No optimization should be retained later unless it improves the relevant end-to-end metric on the mandatory gate cases, preserves numerical certificates and determinism, and has an acceptable memory tradeoff.

## Definition of done

The diagnostic campaign is complete only when all of the following are true:

- [ ] the pending 16-RHS status is resolved and represented as data or an explicit scientific failure;
- [ ] the legacy raw main samples are reduced and committed without altering the first study;
- [ ] every accepted result has exact source and binary identity with no repair;
- [ ] the five-family one-million-vertex replication has seven measurements at 1, 8, 16, and 32 CPUs for both implementations;
- [ ] real production hierarchy, plan, CMG, and PCG phases are profiled;
- [ ] serial, planned, auto, setup-thread, plan-reuse, and workspace-reuse paths are measured;
- [ ] NUMA placement, first touch, active CPU use, effective frequency, and available bandwidth counters are measured or explicitly documented as unsupported;
- [ ] process baseline and incremental memory are separated;
- [ ] native accuracy and tolerance-frontier comparisons are complete;
- [ ] repeated-RHS throughput and memory are measured;
- [ ] all raw repetitions, identities, warnings, and failure records are preserved;
- [ ] deterministic reduction generates the report, PDF, compact JSON, data tables, evidence manifest, and GPT Pro packet;
- [ ] the raw archive is published as a checksummed GitHub release asset;
- [ ] CI is green on the exact results commit;
- [ ] `benchmarks/report2/GPTPRO_HANDOVER.md` contains the final prompt and exact attachment list;
- [ ] no production optimization is mixed into the diagnostic source commit;
- [ ] this file records final run IDs, source SHAs, result commit SHA, release tag, and any incomplete capability.

## Codex execution checklist

### Protocol and instrumentation

- [x] Read the first benchmark report, `GPTPRO_HANDOVER.md`, `docs/PERFORMANCE.md`, and all existing profiling binaries.
- [x] Create the `scc2` protocol and schema files.
- [x] Fix compiled source identity and add binary hashes.
- [x] Add shared-fixture production-path hierarchy profiling.
- [x] Add plan subphase profiling.
- [x] Extend PCG phase profiling to shared fixtures and all families.
- [x] Add process CPU time, memory stages, NUMA metadata, and counter wrappers.
- [x] Extend MATLAB diagnostics and exact warning capture.
- [x] Add validators and unit tests.
- [ ] Push the clean diagnostic implementation to `main` and wait for CI.

### Runs

- [x] Recover and validate the old `batch16` run.
- [x] Extract the old main-run raw samples.
- [ ] Run capability/topology smoke.
- [ ] Run the mandatory one-million-vertex replication.
- [ ] Run Rust route and phase diagnostics.
- [ ] Run decoupled setup/plan/solve and reuse diagnostics.
- [ ] Run NUMA/first-touch diagnostics.
- [ ] Run memory decomposition.
- [ ] Run accuracy frontier.
- [ ] Run repeated-RHS diagnostics.
- [ ] Run matched-edge conditional matrix if the mandatory evidence does not identify the scaling cause.

### Publication

- [ ] Validate every run and retain failures.
- [ ] Generate all reduced data and figures deterministically.
- [ ] Write `METHODOLOGY.md` and a facts-only `FINDINGS.md`.
- [ ] Compile `output/pdf/benchmarks2.pdf`.
- [ ] Create and verify the GPT Pro packet.
- [ ] Publish the checksummed raw release archive.
- [ ] Commit and push all reduced artifacts to `main`.
- [ ] Record final identities and mark this plan complete.
