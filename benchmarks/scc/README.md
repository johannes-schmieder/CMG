# SCC large-scale benchmark workflow

This is the maintained BU SCC workflow for immutable, reproducible CMG
experiments. It compares the current Rust implementation with the official
MATLAB solver and its default C MEX build, and it supports targeted routing,
reuse, NUMA, memory, accuracy, batch, and matched-edge studies.

Every run uses a clean Git archive, a unique UTC run directory, build-time
source and archive identity, canonical binary fixtures, raw timing and process
CPU samples, numerical certificates, and SGE accounting. Unsupported hardware
counters are recorded explicitly rather than converted to zero. The internal
protocol identifier remains `cmg-scc2-v1` so existing run archives stay
verifiable.

## Deploy and smoke-test

For bounded library development, deploy a clean source with `deploy.sh` under
a fresh `-b2v1-development` run ID, then use `submit_development.sh RUN_ID
shutdown|integration` from that deployment. This reserves 28 slots for at most
30 minutes, uses four build jobs and serial test execution, and never starts a
MATLAB build or a historical campaign. `shutdown` runs the worker lifetime
fixtures; `integration` runs the library feature matrix, lint, docs and MSRV
checks. The immutable source is checked before submission and before/after
execution. A reservation prevents duplicate submission, including when the
client loses the response. Inspect the queue and receipt rather than removing
the reservation. Acceptance requires complete zero-exit qacct, empty scheduler
stderr, every expected fixture result/step marker and `DEVELOPMENT_SUCCESS`.
Affinity in the environment record is observed, not claimed exclusive.
The single 32-worker construction test is excluded in this 28-slot lane;
the focused retirement tests cover changing pool sizes up to eight workers.

The `screen` development mode uses `ownership_screen.sh` and the reusable
`ownership-screen` driver. First deploy with `deploy_development_screen.sh
RUN_ID SOURCE_SHA BASELINE_SHA`; this wraps normal deployment and freezes the
reference Git archive separately. The compute-node job compiles identical
harness bytes against both immutable sources, with separate timing and requested
allocation builds, verifies lockfile dependency correspondence and identical
binary fixture inputs, then alternates seven baseline/candidate process pairs.
Its original frozen four-case list is connected path, path with pairs/isolates, dense
worker-firm with pairs, and a tiny weighted bridge, each with ordinary/explicit
component construction and one/four workers. Four caller-buffer solves reuse
each hierarchy and workspace. Output includes setup, workspace, solve, joined
shutdown, total time and separate allocation observations. Version 2 adds a
fifth, separately reported case: the frozen dyadic bridge, whose first
multilevel attempt passes the native backward certificate but fails a stricter
caller residual gate. It measures rejected setup, solve, independent gate check,
complete bank/pool retirement, direct rebuild, solve, acceptance check and final
cleanup. The caller-buffer output is stack storage; input parents are excluded.
Ordinary/component and one/four workers yield 20 configurations and 140 pairs.
Only this added case's total includes all solver-owned cleanup. Neither version
qualifies an automatic production fallback policy. Baseline pool drop has a
different lifetime endpoint: it returns without joining workers. Full sources,
artifacts, input hashes, raw JSONL and validation summary remain in the run.

`integration-screen` performs the full bounded integration matrix first, then
benchmark formatting/Clippy and the version-2 screen in the same allocation.
Deploy with the reference archive. A failing accuracy test stops the job;
timings cannot override it. Keep the historical default-policy bridge diagnosis
separate from the explicit absolute-residual regression described in
[`residual-contract.md`](../../docs/residual-contract.md).

The separate `oracle` development mode uses the same reference-archive deploy
but runs only the frozen 64-vertex dyadic weak-bridge diagnosis, not performance
or a campaign. Default PCG certification, independent path-current residual and
analytic centered resistance voltages are recorded for ordinary/component,
one/four workers and direct thresholds 8/65. The forward limit remains `1e-6`;
the independent residual limit remains `sqrt(2)*1e-8`. The driver exits 2 when
an accuracy gate fails. The wrapper records those exit codes without converting
them into qualification: its receipt is `ORACLE_DIAGNOSIS_COMPLETE`, never
`DEVELOPMENT_SUCCESS`. Unexpected execution errors still fail the job. Preserve
the original failed unit test and inspect both source-bound diagnostic rows.

`storage` is a targeted allocation-API lane: minimal/all-feature/release
`storage_accounting` tests, formatting, Clippy, docs and the exact Rust 1.85.1
compatibility check. Its receipt is `STORAGE_API_VALIDATION_SUCCESS`, explicitly
not full solver qualification. It neither invokes nor disables the separately
failing accuracy oracle, and it performs no performance measurements. Rust
1.85.0 itself is not tested by this patch-version compatibility lane.
When separately authorized, `storage-screen` adds the unchanged four-case
development screen in the same allocation (deploy with the reference archive).
Its timing evidence never overrides an independent accuracy failure or turns
the targeted storage receipt into full solver qualification.

Start from a clean local checkout:

```bash
source_sha=$(git rev-parse HEAD)
run_id="$(date -u +%Y%m%dT%H%M%SZ)-${source_sha:0:12}-b2v1-smoke"
bash benchmarks/scc/deploy.sh "$run_id" "$source_sha"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/bootstrap.sh $run_id"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit.sh smoke $run_id"
```

After the job leaves `qstat`, collect all array-task accounting and validate the
complete run:

```bash
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/collect_accounting.sh RUN_ID JOB_ID 2"
ssh scc "module load python3/3.12.4 && python3 \
  /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/validate_run.py \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID/manifests/tasks/smoke.jsonl \
  JOB_ID"
```

Raw run directories are never overwritten. Retry wrapper, scheduler, or
transport faults under a new run ID; preserve scientific failures as evidence.

## Submit an experiment

`submit.sh` accepts these frozen task families:

| Kind | Scope |
|---|---|
| `baseline` | current Rust versus MATLAB, five 1M-vertex families |
| `routing` | serial, planned, and automatic single-RHS routing |
| `reuse` | hierarchy, plan, and workspace reuse |
| `numa` | placement and memory-policy sensitivity |
| `memory` | separate-process stage memory |
| `accuracy` | time/accuracy frontier |
| `batch` | repeated-RHS scaling |
| `matched-edge` | graph families at approximately equal edge counts |

For example:

```bash
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/submit.sh baseline RUN_ID 4G"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/collect_accounting.sh RUN_ID JOB_ID 5"
ssh scc "module load python3/3.12.4 && python3 \
  /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/validate_run.py \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID/manifests/tasks/baseline.jsonl \
  JOB_ID"
```

The optional third submission argument is memory per core; allowed values are
listed in `submit.sh`. Each task requests 32 Gold-6242 slots with whole-node
linear binding. The task's application-level CPU grid is independent of the SGE
allocation.

## Reduce accepted results

Copy or mount accepted run directories, then generate inspectable CSVs and a
compact reduction receipt:

```bash
python3 benchmarks/scc/analysis/reduce.py \
  --run benchmark-runs/RUN_ID \
  --report-root benchmark-runs/reduced \
  --latest-json benchmark-runs/reduced/receipt.json

python3 benchmarks/scc/analysis/plot.py \
  --report-root benchmark-runs/reduced
```

Use `python3 ... --help` to inspect the exact reducer and plotter interfaces.
Generated raw results, logs, accounting, and exploratory report trees remain
outside the maintained source tree.

## Accepted current qualification

Run `20260828T021628Z-6fe9be77084a-b2v1-rust-matlab-current` used source
`6fe9be77084a60cca330760361dd4c7addc77ccf`, official upstream commit
`19752fc102f8cae8e34f66457bfaccb1aaa60375`, Rust 1.98.0, and MATLAB 2026a.
SGE array `7341600.1-5` completed all five one-million-vertex families and all
40 Rust/MATLAB/thread configurations with clean accounting and scientific
validation. The compact result is
[`scc-rust-matlab-current.json`](../../.ci/performance/scc-rust-matlab-current.json).
