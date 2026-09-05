#!/bin/bash -l
# One-slot shared-host dispatch qualification, intentionally not whole-host screening.
set -euo pipefail
kind=${1:?usage: submit_dispatch.sh KIND CPU_PROFILE RUN_ID [4G]}
profile=${2:?missing CPU_PROFILE}
run_id=${3:?missing RUN_ID}
memory=${4:-4G}
case "$kind" in dispatch-smoke) runtime=00:15:00; tasks=1;; dispatch-validate) runtime=02:00:00; tasks=3;; *) exit 2;; esac
case "$profile" in e5-2680v4) cores=28; cpu=E5-2680v4;; gold-6242) cores=32; cpu=Gold-6242;; *) exit 2;; esac
test "$memory" = 4G
[[ "$run_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1-dispatch$ ]]
project=/projectnb/welfgr/cmg-benchmarks
run="$project/runs/$run_id"
source_sha=$(tr -d '\n' < "$run/manifests/source-commit.txt")
code="$project/code-b2/$source_sha"
# The submit-time validator may be a separately committed accounting-only fix.
# Execution still uses the original run's immutable runner and portable binary.
validator=$(cd "$(dirname "$0")" && pwd -P)
experiment="$kind-$profile"
receipt="$run/manifests/submission-$experiment.txt"
task_file="$run/manifests/tasks/$experiment.jsonl"
test ! -e "$receipt"
test ! -e "$run/output/$experiment"
test ! -e "$run/receipts/$experiment"
test ! -e "$run/logs/$experiment"
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
module purge
module load python3/3.12.4
export PYTHONDONTWRITEBYTECODE=1
validator_identity=$(python3 "$validator/dispatch_validator_reuse.py" "$run" "$validator")
python3 "$validator/dispatch_campaign.py" check-manifest "$task_file" >/dev/null
python3 "$validator/dispatch_campaign.py" gate "$run" "$kind" >/dev/null
# Atomic reservation remains on a failed/ambiguous submit: inspect, never retry blindly.
mkdir "$run/manifests/submission-$experiment.lock"
mkdir "$run/logs/$experiment"
job_id=$(qsub -terse -P welfgr -pe omp 1 -binding linear:1 \
    -l "num_proc=$cores,cpu_type=$cpu" -l mem_per_core=4G -l "h_rt=$runtime" \
    -t "1-$tasks" -tc 1 -N "cmg-b2-$experiment" \
    -o "$run/logs/$experiment" -e "$run/logs/$experiment" \
    -v "CMG_RUN_ID=$run_id,CMG_TASK_FILE=$task_file" "$code/benchmarks/scc/run_task.sh")
[[ "$job_id" =~ ^[0-9]+([.][0-9:-]+)?$ ]]
set -o noclobber
{
    printf 'kind=%s\ncpu_profile=%s\njob_id=%s\ntasks=%s\nslots=1\nmem_per_core=4G\nruntime=%s\n' "$kind" "$profile" "$job_id" "$tasks" "$runtime"
    printf 'task_file=%s\nhost_num_proc=%s\nhost_cpu_type=%s\n' "$task_file" "$cores" "$cpu"
    printf '%s\n' "$validator_identity"
    qstat -j "${job_id%%.*}" || printf 'qstat_snapshot=unavailable_after_submission\n'
} > "$receipt" 2>&1
printf '%s\n' "$job_id"
