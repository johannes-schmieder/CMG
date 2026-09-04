# SCC large-scale benchmark workflow

This is the maintained BU SCC workflow for immutable, reproducible CMG
experiments. It compares the current Rust implementation with the official
MATLAB solver and its default C MEX build, and it supports targeted routing,
reuse, NUMA, memory, accuracy, batch, and matched-edge studies.
The experimental fused-RHS lane adds `fused-smoke` and `fused` kinds. They run
the portable binary on exactly 28-core hosts, using all 28 slots for isolation
while making the SCC's large older-node population eligible. CPU screening adds
profile-specific `fused-cpu-smoke-*` and `fused-cpu-screen-*` experiments. All
fused outputs and receipts are namespaced by experiment so overlapping array
task IDs cannot overwrite one another under one immutable campaign root.

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
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit_bootstrap.sh $run_id 6G"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit.sh smoke $run_id"
```

The bootstrap submitter runs compilation and validation on a four-slot compute
job, initializes Lmod explicitly, and refuses to overwrite an existing attempt
or `BUILD_SUCCESS` receipt. Submit a benchmark only after its complete qacct,
logs, manifests, hashes, and receipt validate. After an array leaves `qstat`,
collect all task accounting and validate the complete run:

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
| `fused-smoke` | 100k-vertex portable width-four numerical and 28-core-host launcher smoke |
| `fused` | paired portable scalar/fused 1M-vertex RHS-count and mix matrix on 28-core hosts |
| `fused-cpu-smoke-*` | one 100k-vertex launcher and host-contract check per CPU profile |
| `fused-cpu-screen-*` | four 1M-vertex RHS-16 sparse/dense and homogeneous/mixed cases per CPU profile |

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
listed in `submit.sh`. Most SCC2 tasks retain the established 32-slot Gold-6242
request. The fused kinds instead request 28 slots with whole-node linear binding,
`num_proc=28`, and `cpu_type=E5-2680v4`, and execute only the portable binary.
This targets the SCC's large older Broadwell population while preserving paired
scalar/fused measurements on consistent hardware.

## Cross-CPU fused screening

The CPU screen re-runs Broadwell alongside representative SCC generations so
every hardware comparison uses one source commit, archive, and portable binary.
Profiles and their exact scheduler/core/model contracts live in
`fused_cpu_profiles.json`; do not reproduce or override that mapping in an ad
hoc `qsub` command. The maintained profiles are E5-2650v2, E5-2680v4,
Gold-6132, Gold-6242, Gold-6326, and EPYC-9124.

After the common compute-node bootstrap passes, submit all one-task CPU smokes
through the dedicated guarded entrypoint:

```bash
for profile in e5-2650v2 e5-2680v4 gold-6132 gold-6242 gold-6326 epyc-9124; do
  ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/submit_fused_cpu.sh fused-cpu-smoke $profile RUN_ID 4G"
done
```

Require complete successful accounting, empty scheduler stderr, application and
validation success markers, exact output/receipt counts, bitwise identity, and
the manifest's CPU model/core/affinity contract for every profile. Only after
all six smokes pass, submit the four-task screens exactly once:

```bash
for profile in e5-2650v2 e5-2680v4 gold-6132 gold-6242 gold-6326 epyc-9124; do
  ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/SOURCE_SHA/benchmarks/scc/submit_fused_cpu.sh fused-cpu-screen $profile RUN_ID 4G"
done
```

Each profile reserves and binds the whole matching host and runs one array task
at a time. Use one campaign monitor for the common bootstrap, all CPU smokes,
and all CPU screens. If any smoke fails, preserve the run and do not submit any
screen. A missing submission response is ambiguous: inspect the profile's
`submission-*` receipt and exact queued arguments before retrying.

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
