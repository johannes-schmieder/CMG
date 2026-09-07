#!/bin/bash -l
# Guarded submit/accept entrypoint for the separately frozen component study.
set -euo pipefail
action=${1:?submit or accept}
run_id=${2:?RUN_ID}
stage=${3:?STAGE}
profile=${4:?PROFILE}
case "$action" in submit|accept) ;; *) exit 2 ;; esac
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
module purge
module load python3/3.12.4
project_root=/projectnb/welfgr/cmg-benchmarks
source_sha=$(tr -d '\n' < "$project_root/runs/$run_id/manifests/source-commit.txt")
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$project_root/code-b2/$source_sha/benchmarks/scc/component_campaign.py" "$action" "$run_id" "$stage" "$profile"
