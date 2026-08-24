#!/bin/bash
set -euo pipefail

run_id=${1:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
job_id=${2:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
task_count=${3:?usage: collect_accounting.sh RUN_ID JOB_ID TASK_COUNT}
receipt_root=/projectnb/welfgr/cmg-benchmarks/runs/$run_id/receipts
mkdir -p "$receipt_root/accounting"
for task_id in $(seq 1 "$task_count"); do
    qacct -j "$job_id" -t "$task_id" > "$receipt_root/accounting/$job_id.$task_id.txt"
done
echo "CMG_ACCOUNTING_COLLECTED job=$job_id tasks=$task_count"
