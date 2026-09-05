#!/bin/bash
# Submit one immutable SCC2 bootstrap to a compute node.
set -euo pipefail

run_id=${1:?usage: submit_bootstrap.sh RUN_ID [MEM_PER_CORE]}
mem_per_core=${2:-6G}
case "$mem_per_core" in
    3G|4G|6G|8G|12G|16G|18G|28G) ;;
    *) echo "unsupported mem_per_core value: $mem_per_core" >&2; exit 2 ;;
esac

project_root=/projectnb/welfgr/cmg-benchmarks
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_sha"
bootstrap="$code_root/benchmarks/scc/bootstrap.sh"
receipt="$run_root/manifests/submission-bootstrap.txt"

test -x "$bootstrap"
test ! -e "$receipt"
test ! -e "$run_root/receipts/BUILD_SUCCESS"

job_id=$(qsub -terse -P welfgr -pe omp 4 -binding linear:4 \
    -l mem_per_core="$mem_per_core" -l h_rt=02:00:00 \
    -N cmg-b2-bootstrap -o "$run_root/logs" -e "$run_root/logs" \
    "$bootstrap" "$run_id")
base_job_id=${job_id%%.*}
temporary="$receipt.tmp"
{
    printf 'kind=bootstrap\njob_id=%s\nslots=4\nruntime=02:00:00\nmem_per_core=%s\n' \
        "$job_id" "$mem_per_core"
    qstat -j "$base_job_id"
} > "$temporary" 2>&1
mv "$temporary" "$receipt"
printf '%s\n' "$job_id"
