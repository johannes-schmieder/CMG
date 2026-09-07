#!/bin/bash -l
# Run only on an SGE compute allocation; scheduler binding is intentionally omitted.
set -euo pipefail
run_id=${1:?RUN_ID}
stage=${2:?STAGE}
profile=${3:?PROFILE}
test -n "${JOB_ID:-}"
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
module purge
module load python3/3.12.4
project_root=/projectnb/welfgr/cmg-benchmarks
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
export PYTHONPYCACHEPREFIX="$run_root/work/python-cache-${JOB_ID}-${SGE_TASK_ID:-0}"
exec python3 "$project_root/code-b2/$source_sha/benchmarks/scc/component_campaign.py" run "$run_id" "$stage" "$profile"
