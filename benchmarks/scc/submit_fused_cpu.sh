#!/bin/bash -l
# Submit one guarded, CPU-specific fused-RHS smoke or screening array.
set -euo pipefail

kind=${1:?usage: submit_fused_cpu.sh KIND CPU_PROFILE RUN_ID [MEM_PER_CORE]}
cpu_profile=${2:?usage: submit_fused_cpu.sh KIND CPU_PROFILE RUN_ID [MEM_PER_CORE]}
run_id=${3:?usage: submit_fused_cpu.sh KIND CPU_PROFILE RUN_ID [MEM_PER_CORE]}
mem_per_core=${4:-4G}
case "$kind" in
    fused-cpu-smoke|fused-cpu-screen) ;;
    *) echo "unsupported fused CPU kind: $kind" >&2; exit 2 ;;
esac
case "$cpu_profile" in
    ''|*[!a-z0-9-]*) echo "invalid CPU profile: $cpu_profile" >&2; exit 2 ;;
esac
case "$mem_per_core" in
    3G|4G|6G|8G|12G|16G|18G|28G) ;;
    *) echo "unsupported mem_per_core value: $mem_per_core" >&2; exit 2 ;;
esac

project_root=/projectnb/welfgr/cmg-benchmarks
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_sha"
experiment="$kind-$cpu_profile"
task_file="$run_root/manifests/tasks/$experiment.jsonl"
receipt="$run_root/manifests/submission-$experiment.txt"
log_root="$run_root/logs/$experiment"

test -f "$run_root/receipts/BUILD_SUCCESS"
test -f "$task_file"
test ! -e "$receipt"
test ! -e "$run_root/output/$experiment"
test ! -e "$run_root/receipts/$experiment"

if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
type module >/dev/null 2>&1
module purge
module load python3/3.12.4
python3 "$code_root/benchmarks/scc/validate_fused_manifest.py" "$task_file" >/dev/null
mapfile -t resources < <(python3 - "$task_file" <<'PY'
import json
import sys

rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
for key in ("slots", "host_num_proc", "host_cpu_type", "cpu_model_contains"):
    values = {str(row[key]) for row in rows}
    if len(values) != 1:
        raise SystemExit(f"inconsistent {key}")
    print(values.pop())
PY
)
slots=${resources[0]}
host_num_proc=${resources[1]}
host_cpu_type=${resources[2]}
cpu_model_contains=${resources[3]}
tasks=$(wc -l < "$task_file" | tr -d ' ')
case "$kind" in
    fused-cpu-smoke) runtime=00:30:00 ;;
    fused-cpu-screen) runtime=02:00:00 ;;
esac

mkdir -p "$log_root"
job_id=$(qsub -terse -P welfgr -pe omp "$slots" -binding "linear:$slots" \
    -l "num_proc=$host_num_proc,cpu_type=$host_cpu_type" \
    -l mem_per_core="$mem_per_core" -l h_rt="$runtime" \
    -t "1-$tasks" -tc 1 -N "cmg-b2-$kind-$cpu_profile" \
    -o "$log_root" -e "$log_root" \
    -v "CMG_RUN_ID=$run_id,CMG_TASK_FILE=$task_file" \
    "$code_root/benchmarks/scc/run_task.sh")
base_job_id=${job_id%%.*}
case "$base_job_id" in
    ''|*[!0-9]*) echo "invalid qsub response: $job_id" >&2; exit 1 ;;
esac
temporary="$receipt.tmp"
{
    printf 'kind=%s\nexperiment=%s\ncpu_profile=%s\njob_id=%s\ntask_file=%s\ntasks=%s\n' \
        "$kind" "$experiment" "$cpu_profile" "$job_id" "$task_file" "$tasks"
    printf 'runtime=%s\nmem_per_core=%s\nslots=%s\nhost_num_proc=%s\n' \
        "$runtime" "$mem_per_core" "$slots" "$host_num_proc"
    printf 'host_cpu_type=%s\ncpu_model_contains=%s\n' \
        "$host_cpu_type" "$cpu_model_contains"
    if ! qstat -j "$base_job_id"; then
        printf 'qstat_snapshot=unavailable_after_accepted_submission\n'
    fi
} > "$temporary" 2>&1
mv "$temporary" "$receipt"
printf '%s\n' "$job_id"
