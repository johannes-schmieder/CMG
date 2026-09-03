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

## Monitoring and recovery

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
