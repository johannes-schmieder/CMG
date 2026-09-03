#!/bin/bash
set -euo pipefail

kind=${1:?usage: submit.sh KIND RUN_ID [MEM_PER_CORE]}
run_id=${2:?usage: submit.sh KIND RUN_ID [MEM_PER_CORE]}
mem_per_core=${3:-4G}
case "$mem_per_core" in
    3G|4G|6G|8G|12G|16G|18G|28G) ;;
    *) echo "unsupported mem_per_core value: $mem_per_core" >&2; exit 2 ;;
esac
project_root=/projectnb/welfgr/cmg-benchmarks
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_sha"
task_file="$run_root/manifests/tasks/$kind.jsonl"
test -f "$run_root/receipts/BUILD_SUCCESS"
test -f "$task_file"
tasks=$(wc -l < "$task_file" | tr -d ' ')
case "$kind" in
    smoke|baseline|routing|reuse|numa|memory|accuracy) runtime=02:00:00 ;;
    batch|matched-edge|fused) runtime=04:00:00 ;;
    fused-smoke) runtime=02:00:00 ;;
    *) echo "unknown experiment $kind" >&2; exit 2 ;;
esac
case "$kind" in
    fused|fused-smoke)
        slots=28
        host_requirement=num_proc=28
        ;;
    *)
        slots=32
        host_requirement=cpu_type=Gold-6242
        ;;
esac
job_id=$(qsub -terse -P welfgr -pe omp "$slots" -binding "linear:$slots" \
    -l "$host_requirement" -l mem_per_core="$mem_per_core" -l h_rt="$runtime" \
    -t "1-$tasks" -tc 2 -N "cmg-b2-$kind" \
    -o "$run_root/logs" -e "$run_root/logs" \
    -v "CMG_RUN_ID=$run_id,CMG_TASK_FILE=$task_file" \
    "$code_root/benchmarks/scc/run_task.sh")
base_job_id=${job_id%%.*}
{
    printf 'kind=%s\njob_id=%s\ntask_file=%s\ntasks=%s\nruntime=%s\nmem_per_core=%s\nslots=%s\nhost_requirement=%s\n' \
        "$kind" "$job_id" "$task_file" "$tasks" "$runtime" "$mem_per_core" "$slots" "$host_requirement"
    qstat -j "$base_job_id"
} > "$run_root/manifests/submission-$kind.txt" 2>&1
printf '%s\n' "$job_id"
