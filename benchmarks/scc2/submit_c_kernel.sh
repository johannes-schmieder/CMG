#!/bin/bash
set -euo pipefail

run_id=${1:?usage: submit_c_kernel.sh RUN_ID}
project_root=/projectnb/welfgr/cmg-benchmarks
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_sha"
test -f "$run_root/receipts/C_KERNEL_BUILD_SUCCESS"

job_id=$(qsub -terse -P welfgr -pe omp 32 -binding linear:32 \
    -l cpu_type=Gold-6242 -l mem_per_core=3G -l h_rt=00:30:00 \
    -N cmg-b2-c-kernel -o "$run_root/logs" -e "$run_root/logs" \
    -v "CMG_RUN_ID=$run_id,CMG_REPETITIONS=7" \
    "$code_root/benchmarks/scc2/run_c_kernel.sh")
base_job_id=${job_id%%.*}
{
    printf 'job_id=%s\nslots=32\nmem_per_core=3G\nruntime=00:30:00\nrepetitions=7\n' \
        "$job_id"
    qstat -j "$base_job_id"
} > "$run_root/manifests/submission-c-kernel.txt" 2>&1
printf '%s\n' "$job_id"
