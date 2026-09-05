#!/bin/bash -l
set -euo pipefail

project_root=/projectnb/welfgr/cmg-benchmarks
run_id=${CMG_RUN_ID:?CMG_RUN_ID is required}
task_file=${CMG_TASK_FILE:?CMG_TASK_FILE is required}
task_id=${SGE_TASK_ID:-${CMG_LOCAL_TASK_ID:-1}}
source_commit=$(tr -d '\n' < "$project_root/runs/$run_id/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_commit"

if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
module purge
module load python3/3.12.4
export RUSTUP_HOME="$project_root/toolchains/rustup"
export CARGO_HOME="$project_root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH"

experiment=$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).read().splitlines()[int(sys.argv[2])-1])["experiment"])' "$task_file" "$task_id")
case "$experiment" in
    dispatch-smoke-*|dispatch-validate-*)
        export PYTHONDONTWRITEBYTECODE=1
        python3 "$code_root/benchmarks/scc/dispatch_campaign.py" run \
            "$project_root/runs/$run_id" "$task_file" "$task_id"
        python3 "$code_root/benchmarks/scc/dispatch_campaign.py" validate \
            "$project_root/runs/$run_id" "$task_file" "$task_id"
        exit 0
        ;;
    fused|fused-smoke|fused-cpu-smoke-*|fused-cpu-screen-*)
        python3 "$code_root/benchmarks/scc/run_fused_task.py" "$run_id" "$task_file" "$task_id"
        python3 "$code_root/benchmarks/scc/validate_fused_task.py" \
            "$project_root/runs/$run_id" "$task_file" "$task_id"
        exit 0
        ;;
esac

module load "$(tr -d '\n' < "$project_root/toolchains/matlab-module.txt")"

python3 "$code_root/benchmarks/scc/run_task.py" "$run_id" "$task_file" "$task_id"
python3 "$code_root/benchmarks/scc/validate_task.py" \
    "$project_root/runs/$run_id" "$task_file" "$task_id"
