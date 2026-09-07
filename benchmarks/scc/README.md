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

## Component-default qualification (September 2026)

This is a **separate campaign** from fused RHS and calibrated dispatch. It tests
unchanged checkpoint `90d06d58edf7de43e6e78855b1b14c4b6311b808` against candidate B
`1d0396f805fb358106c592c6362506d3cb01517d` using the matched ordinary caller and
accuracy drivers. Only repository-generated synthetic graphs and RHSs transfer;
there is no Veneto input, restricted data, MATLAB comparison, numerical change,
or automatic promotion. Earlier accepted and failed campaigns stay immutable.

`component_campaign.py` is the frozen specification. The two profiles are
Broadwell E5-2680v4 (28 slots) and Cascade Lake Gold-6242 (32 slots). Each benchmark
allocation requests `exclusive=true` and the exact whole-host CPU/core count.
SCC's global configuration does not enable scheduler binding. The launcher
therefore omits `-binding`, explicitly pins each process and its children to the
first one or four physical cores of the first socket, verifies the affinity,
and checks at start/end that `qhost` lists no other scheduler job on that host.
An exclusive reservation and these checks reduce host contention; they cannot
remove operating-system activity or establish performance on all x86 hardware.

Use one fresh `*-b2v1-component-default` run. The existing `deploy.sh` stages the
canonical plan plus exact Git archives of both numerical sources. The guarded
`submit_component.sh` owns submission reservations, captures the exact request,
and refuses duplicate submissions or existing output namespaces. Never remove a
reservation after an ambiguous response; inspect its recorded response and queue.

1. Commit the clean launcher/driver source, then deploy with `deploy.sh RUN SHA`.
2. Submit `submit_component.sh submit RUN bootstrap all`. This reserves four
   compute slots and 6G per slot for at most one hour, uses the already-installed
   project Rust 1.98.0 toolchain, and builds matched minimal/parallel binaries
   with `-C target-cpu=x86-64`. Library and driver tests, formatting and Clippy
   run on the compute node. Source extraction and builds remain run-scoped;
   the immutable deployed code and source archives are never patched.
3. After completion use `submit_component.sh accept RUN bootstrap all`. Require
   complete successful accounting, empty scheduler stderr, the unique bootstrap
   marker, checksummed build logs, source correspondence and binary identities.
4. Submit both `submit_component.sh submit RUN smoke PROFILE` jobs. The smoke
   includes the expensive path-plus-pairs, 16-RHS/four-worker boundary, ordinary
   owning and buffer callers, and both-source accuracy diagnostics. Each smoke
   reserves the same exclusive host used by qualification, with a one-hour cap.
5. Accept **both** smokes using the corresponding `accept` commands before any
   validation submission. A failed smoke stops the campaign.
6. Submit `submit_component.sh submit RUN validate PROFILE` once per profile.
   Each array has exactly 37 tasks, a six-hour task cap, 3G per reserved slot,
   and concurrency capped at two tasks per profile. Twenty-seven tasks cover
   the three previously omitted mixed graphs across nine caller configurations:
   minimal owning/buffer at 4/16 RHSs, parallel-feature buffer at four RHSs, and
   planned one/four-worker callers at 4/16 RHSs. Nine tasks bundle six connected
   controls at those same configurations. The last task independently checks
   the large dense connected one-RHS buffer control. There are 82 cells per CPU.
7. After complete per-task accounting, accept each array. `component_campaign.py
   summary RUN` emits the combined report only after both arrays are accepted.

The 27 costly omitted-case cells have 12 balanced AB/BA blocks, two warmups and
one recorded repetition per invocation. The connected bundles use 12 blocks and
three recorded repetitions. The dense one-RHS control uses 30 blocks and 20
recorded repetitions. These are frozen before SCC measurement; there are no
extensions or result-dependent tuning. A block pairs checkpoint and B invocation
medians. Report their median ratio and a fixed-seed 10,000-resample paired 95%
interval, by CPU/case/caller/RHS/worker count. These intervals are exploratory and
not adjusted for multiple comparisons. A connected cell passes only when its
lower speedup bound is at least `1/1.02`; a target gain requires a lower bound of
at least 1.05. A failure or inconclusive gate is reported, never repeated until it
passes. Smoke timings are operational diagnostics, not qualifying measurements.

Every raw invocation must have exact case/source/build configuration, positive
phase timings, valid residual certificates and repeated/cross-source numerical
fingerprints. A difference stops that task for investigation. The accuracy
screen separately evaluates the original and `1e-12` stopping tolerances; it does
not change production settings or turn a residual certificate into a guarantee
about coefficient error. Retain all raw JSONL, affinity records, scheduler logs,
complete qacct, source/binary manifests and stage receipts. Summaries must report
walltime/maxvmem ranges and distinguish scientific acceptance from performance
qualification. Do not run the older README's direct-login bootstrap command for
this campaign.
