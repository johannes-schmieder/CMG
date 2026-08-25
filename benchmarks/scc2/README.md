# SCC2 CMG diagnostics

This directory implements the immutable second SCC campaign described in
[`benchmarks2.md`](../../benchmarks2.md). It is intentionally separate from the
accepted first-study harness under `benchmarks/scc/`.

Each accepted array uses a clean Git archive, an immutable UTC run directory,
build-time source/archive identity, canonical binary fixtures, raw timing and
process-CPU samples, topology-derived CPU lists, numerical certificates, and
SGE accounting. Unsupported hardware counters are recorded explicitly by
`capabilities.py` rather than converted to zero.

Typical use from a clean local checkout:

```bash
source_sha=$(git rev-parse HEAD)
run_id="$(date -u +%Y%m%dT%H%M%SZ)-${source_sha:0:12}-b2v1-smoke"
bash benchmarks/scc2/deploy.sh "$run_id" "$source_sha"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc2/bootstrap.sh $run_id"
ssh scc "bash /projectnb/welfgr/cmg-benchmarks/code-b2/$source_sha/benchmarks/scc2/submit.sh smoke $run_id"
```

After the array leaves `qstat`, collect `qacct` and validate it:

```bash
bash benchmarks/scc2/collect_accounting.sh RUN_ID JOB_ID TASK_COUNT
python3 benchmarks/scc2/validate_run.py \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID \
  /projectnb/welfgr/cmg-benchmarks/runs/RUN_ID/manifests/tasks/smoke.jsonl JOB_ID
```

Raw run directories are never overwritten. A failed wrapper, scheduler, or
scientific run receives a new run ID.
