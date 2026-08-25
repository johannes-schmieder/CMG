#!/bin/bash -l
set -euo pipefail

project_root=/projectnb/welfgr/cmg-benchmarks
run_id=${CMG_RUN_ID:?CMG_RUN_ID is required}
task_file=${CMG_TASK_FILE:?CMG_TASK_FILE is required}
task_id=${SGE_TASK_ID:-${CMG_LOCAL_TASK_ID:-1}}
source_commit=$(tr -d '\n' < "$project_root/runs/$run_id/manifests/source-commit.txt")
code_root="$project_root/code-b2/$source_commit"

module purge
module load python3/3.12.4
module load "$(tr -d '\n' < "$project_root/toolchains/matlab-module.txt")"
export RUSTUP_HOME="$project_root/toolchains/rustup"
export CARGO_HOME="$project_root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH"

python3 "$code_root/benchmarks/scc2/run_task.py" "$run_id" "$task_file" "$task_id"
python3 "$code_root/benchmarks/scc2/validate_task.py" \
    "$project_root/runs/$run_id" "$task_file" "$task_id"
