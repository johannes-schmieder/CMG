#!/bin/bash
set -euo pipefail

run_id=${1:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
job_id=${2:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
task_count=${3:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
job_id=${job_id%%.*}
receipt_root=/projectnb/welfgr/cmg-benchmarks/runs/$run_id/receipts/accounting
mkdir -p "$receipt_root"
for task_id in $(seq 1 "$task_count"); do
    temporary="$receipt_root/$job_id.$task_id.tmp"
    qacct -j "$job_id" -t "$task_id" > "$temporary"
    mv "$temporary" "$receipt_root/$job_id.$task_id.txt"
done
echo "CMG_SCC2_ACCOUNTING_SUCCESS job=$job_id tasks=$task_count"
