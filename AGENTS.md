# CMG repository instructions

These instructions apply to the whole repository. Read `benchmarks/scc/README.md`
and the SCC entrypoints before changing or running the large-scale benchmark
workflow.

## General rules

- Preserve unrelated user changes. Do not deploy from a dirty worktree.
- Commit every source or launcher change before SCC deployment. SCC evidence is
  tied to the exact commit and Git archive; never patch a deployed source tree.
- Keep failed and accepted SCC runs immutable. A launcher, environment, or
  validation fix requires a new run ID and a fresh deployment.
- Use the repository's guarded scripts. Do not replace them with ad hoc `qsub`,
  manual archive extraction, or direct computation on a login node.
- Never infer success from a job disappearing from `qstat`. Require complete
  `qacct`, application logs, validation messages, outputs, and receipts.

## Fused-RHS SCC campaign

The experimental fused-RHS campaign is a staged evidence workflow:

1. Deploy one clean immutable commit.
2. Run and validate the four-core compute-node bootstrap.
3. Run and validate the two-task `fused-smoke` array on 28-core Broadwell hosts.
4. Only after smoke acceptance, run the 12-task `fused` matrix.
5. Validate and summarize the full matrix without modifying its evidence.

Do not skip a stage. Do not submit the smoke and full arrays together. Use one
campaign monitor, not separate or duplicate monitors for each stage.

### 1. Commit and deploy

The deployment script rejects a dirty worktree and generates frozen task
manifests from the committed source:

```bash
git status --short
source_sha=$(git rev-parse HEAD)
run_stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_id="${run_stamp}-${source_sha:0:7}-b2v1-fused-broadwell"
bash benchmarks/scc/deploy.sh "$run_id" "$source_sha"
```

Record the run ID, full source SHA, and reported archive SHA-256. Verify the
remote `source-commit.txt`, `source-archive-sha256.txt`, and archive hash before
submitting work.

### 2. Bootstrap on a compute node

Never execute `bootstrap.sh` directly over SSH. A non-login remote shell may
not define `module`, and builds must not run on an SCC login node. Submit through
the guarded wrapper:

```bash
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit_bootstrap.sh $run_id 6G"
```

Capture the numeric job ID from stdout and verify
`manifests/submission-bootstrap.txt`. The bootstrap uses four slots, initializes
Lmod itself when necessary, and refuses to overwrite an existing submission or
`BUILD_SUCCESS` receipt. It may run on any eligible compute host; the Broadwell
hardware requirement begins with `fused-smoke`, not with compilation.

The bootstrap redirects all expected `rustup` chatter to
`logs/rustup.log`. Do not weaken that behavior: `rustup` normally writes benign
status lines to stderr, which previously contaminated the scheduler error log.
`set -e` must continue to propagate a real toolchain failure.

After the job leaves `qstat`, accept the bootstrap only when all of these hold:

- `qacct -j JOB_ID` is structurally complete, with `failed=0` and
  `exit_status=0`.
- The scheduler stderr file is empty.
- Scheduler stdout contains exactly one `CMG_SCC2_BOOTSTRAP_SUCCESS` marker.
- `receipts/BUILD_SUCCESS` exists and matches the source and archive identities.
- `logs/rustup.log` exists, is nonempty, and contains no failure.
- Build, test, formatting, Clippy, Python, identity, and task-generation logs
  exist and show successful completion.
- The source archive's actual SHA-256 matches its manifest.
- The portable `fused-rhs-experiment` binary's actual SHA-256 matches
  `manifests/fused-portable-binary-sha256.txt`.

If any gate fails, preserve the run, stop before benchmark submission, make one
narrow local fix, commit it, and start a new immutable run.

### 3. Broadwell smoke

Before calling `submit.sh`, check that the matching submission receipt does not
exist. Unlike the bootstrap wrapper, benchmark submission must be guarded by the
caller to avoid duplicate arrays:

```bash
run_root="/projectnb/welfgr/cmg-benchmarks/runs/$run_id"
ssh scc "test ! -e $run_root/manifests/submission-fused-smoke.txt"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit.sh fused-smoke $run_id 4G"
```

If a submission command gives an ambiguous response or its receipt is missing,
inspect `qstat -u johannes` and the exact job arguments before retrying. A lost
client response does not prove that `qsub` failed.

The fused kinds intentionally differ from the older SCC2 kinds:

- Request exactly 28 slots, not 32.
- Require `num_proc=28,cpu_type=E5-2680v4`.
- Use the portable binary, not the Cascade Lake build.
- Preserve the linear 28-CPU binding and the submitter's task concurrency cap.

A 32-slot request targets the smaller, newer Gold-6242 population and can wait
much longer. A bare 28-slot request is also insufficient because SCC has both
Broadwell and Skylake 28-core hosts. Do not add a queue restriction; the exact
host resources and `-P welfgr` are sufficient.

The smoke array has two tasks. After it leaves `qstat`, wait for complete qacct
records for both task IDs and then require:

- `failed=0`, `exit_status=0`, and `slots=28` for both tasks.
- Empty scheduler stderr and both `CMG_FUSED_TASK_SUCCESS` and
  `CMG_FUSED_VALIDATE_SUCCESS` in each stdout log.
- Exactly two `output/fused-smoke/task-*/fused.json` files and two
  `receipts/fused-smoke/task-*/SUCCESS` files.
- Exact task-manifest, source, archive, and portable-binary correspondence.
- `bitwise_identical=true`, positive timings, a finite paired-bootstrap interval
  containing the reported ratio, and positive fused workspace memory.
- `allocated_slots=28`, 28 affinity CPUs, `host_num_proc=28`, a nonempty
  hostname, and a CPU model containing `E5-2680 v4`.
- No task artifacts under `output/fused/` yet.

Smoke and full task IDs overlap. Their experiment-scoped output and receipt
directories are mandatory; never read from or write to an unscoped
`output/task-*` or `receipts/task-*` path.

### 4. Full matrix

Submit the full matrix exactly once only after every smoke gate passes:

```bash
ssh scc "test ! -e $run_root/manifests/submission-fused.txt"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit.sh fused $run_id 4G"
```

The full array has 12 tasks: two graph families, RHS counts 4/16/32, and
homogeneous/mixed modes. Do not submit extra tasks or a second copy of the
matrix. If the full submission response is ambiguous, inspect the queue and
receipt before taking any action.

For array accounting, do not treat partial qacct output as terminal. Require one
complete record for every expected task ID. After all records exist, the
project helper may record them:

```bash
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/collect_accounting.sh $run_id JOB_ID 12"
```

Apply all smoke acceptance checks to all 12 full tasks. In addition, require
the manifest's exact repetition counts, finite confidence intervals containing
the point estimates, and exactly 12 full outputs and success receipts. Report
fused/scalar ratios and 95% intervals by family, RHS count, mode, and observed
CPU model; classify a clear gain only when the interval's upper bound is below
1 and a clear regression only when its lower bound is above 1. Also report the
qacct walltime/maxvmem ranges and the observed RHS crossover.

## Cross-CPU fused-RHS screening campaign

Treat cross-CPU screening as a separate immutable campaign; never add tasks to
or reuse an accepted Broadwell run. The purpose is to compare one exact portable
binary across representative SCC hardware before designing automatic dispatch.
The checked-in `benchmarks/scc/fused_cpu_profiles.json` is authoritative for
profile names, scheduler CPU types, core counts, slots, and model substrings.

The required stages are:

1. Commit and deploy one clean source snapshot under a new run ID.
2. Submit and fully validate the common four-core bootstrap.
3. Submit one `fused-cpu-smoke` task for every checked-in CPU profile.
4. Wait for every profile smoke to pass all gates; if any fails, stop without
   submitting a screen.
5. Submit one four-task `fused-cpu-screen` array for every profile.
6. Validate and compare all profiles without modifying remote evidence.

Use the dedicated guarded submitter, not `submit.sh` or direct `qsub`:

```bash
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit_fused_cpu.sh fused-cpu-smoke PROFILE $run_id 4G"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc/submit_fused_cpu.sh fused-cpu-screen PROFILE $run_id 4G"
```

Before every invocation, check that the exact
`manifests/submission-KIND-PROFILE.txt` receipt, output namespace, and receipt
namespace do not exist. If the client response is ambiguous, inspect those
paths and `qstat -u johannes` before any retry. Never submit a screen for only a
subset of profiles after a smoke failure unless the user explicitly narrows the
scientific comparison.

Each task must match the manifest's exact whole-host request and must report the
same portable binary hash. Apply the fused acceptance checks to every task,
including complete qacct, empty stderr, both success markers, exact manifest
correspondence, bitwise identity, finite paired intervals, positive workspace
memory, bound-CPU count, host processor count, CPU model, and hostname. Also
require task-result hostnames to agree with qacct. Keep experiment-specific logs,
outputs, receipts, and submission records disjoint across profiles.

The screen has four RHS-16 cases per profile: worker-firm and dense-worker-firm,
each in homogeneous and mixed modes. Summarize ratios and confidence intervals
by CPU, graph family, and mode. Use the result only to decide which CPUs and
density region need a follow-up sweep; two graph-density endpoints do not by
themselves identify a production dispatch threshold.

## Monitoring and recovery

### Calibrated-dispatch qualification exception

The separately authorized dispatch study uses `submit_dispatch.sh`, not either
older fused submitter. Read the calibrated-dispatch section of the SCC README.
Its authoritative canonical matrix is in `dispatch_campaign.py`: two Intel
profiles only (E5-2680v4, Gold-6242), exactly one bound slot and 4G per benchmark
task on shared hosts. The four-slot bootstrap remains mandatory, followed by
both one-task smokes, then three eight-case validation allocations per CPU.
This is an intentional exception to whole-host isolation, not permission to
change the frozen fused profiles, include EPYC, rerun completed evidence, or
expand the validation matrix. Use a fresh `-b2v1-dispatch` run. Never remove
submission reservations or overwrite artifacts/accounting. Keep policy and
promotion gates frozen before qualification and report shared-host limitations.

### Authorized accounting-only continuation (2026-09-05)

The user explicitly authorized reuse of the successful bootstrap 7469156 for
`20260905T151045Z-becd4ac-b2v1-dispatch`. Its numerical source remains
`becd4ac569c93aa26c6b07030cad0c08352cd4a4`; do not rebuild or rerun it.
The only defect was trailing whitespace in `qacct` fields, not failed execution.
Deploy the committed accounting fix separately with `deploy.sh` under a fresh
`-b2v1-dispatch-validator` deployment ID, without submitting its bootstrap.
Use that deployment's guarded `submit_dispatch.sh` for the original run's
unstarted tasks. Its reuse verifier requires exact archived/deployed bytes,
unchanged numerical code, runner, manifests and scientific gates; only the
accounting parser and explicitly listed continuation/docs/tests may differ.
Submission receipts record both validator and numerical identities. Original
evidence and deployed code remain immutable. This narrow exception does not
authorize scientific changes, threshold changes, or replaying completed jobs.
See the SCC README for the continuation commands and frozen promotion gates.

### Authorized serial-launcher retry (2026-09-05)

After smokes 7469361/7469362 failed before numerical execution, the user
authorized a launcher-only correction and one retry, reusing the built binary.
SCC removes the PE from one-slot jobs, so `NSLOTS` can be absent. Submit serial
jobs with `-binding env linear:1`, then explicitly bind only the single OS CPU
listed by `SGE_BINDING`; never choose an arbitrary core or weaken affinity checks.
Log raw NSLOTS/PE/binding, initial/final affinity and the helper revision before
normalizing missing NSLOTS to one and executing the original immutable runner.

Deploy the committed helper separately, without a build. Its guarded
`dispatch_serial_retry.py prepare` creates only
`20260905T151045Z-becd4ac-b2v1-dispatch-serial1`. It first verifies the successful
bootstrap and both exact pre-computation failures, then links the original
build evidence and task manifests with a checksummed `reused-build.json` record.
The original failed run is untouched; new submissions, logs and outputs go to
the retry root. Use the helper's `submit_dispatch.sh` and
`dispatch_serial_retry.py gate/accept/summary` so launcher provenance is also
checked. Both retry smokes must pass before the unchanged validation matrix.
No additional retry, numerical change, extra CPU, or threshold change is implied.

The `serial1` retest also failed before numerical execution. Its raw provenance
showed `NSLOTS=1`, no `SGE_BINDING`, and unrestricted 28/32-CPU affinity. The
installed SGE manual says binding is ignored unless `execd_params` includes
`ENABLE_BINDING`; global SCC configuration lacked it and both execution hosts
had no local override. Check this setting before relying on any `-binding`
request. A whole-host-sized affinity mask alone does not prove that binding was
applied. Do not retry the scheduler-binding approach or infer a reserved physical
core from a one-slot allocation. Application-managed, nonexclusive CPU pinning
needs an explicitly authorized fresh attempt and honest binding provenance.

- Poll at a useful interval (normally ten minutes for this campaign) and keep
  one monitor responsible for the whole bootstrap-smoke-full progression.
- A job absent from `qstat` remains pending until all expected qacct records are
  complete; accounting can lag scheduler disappearance.
- Never create another submission merely because state is unchanged or qacct is
  delayed.
- On bootstrap or smoke failure, stop before the next stage. On a full-task
  failure, preserve every task's logs and outputs and diagnose the exact task IDs.
- Do not overwrite remote logs, manifests, outputs, receipts, accounting, or
  failed evidence. Do not use broad `qdel`, deletion, or sync-with-delete commands.
- Ignore unrelated jobs owned by the user unless they materially block the
  authorized campaign; never cancel or modify them.
