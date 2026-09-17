#!/bin/bash
# One bounded development allocation; never invokes the scientific campaigns.
set -euo pipefail
run_id=${1:?usage: submit_development.sh RUN_ID shutdown|integration}
mode=${2:?usage: submit_development.sh RUN_ID shutdown|integration|screen}
[[ "$run_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1-development$ ]]
[[ "$mode" == shutdown || "$mode" == integration || "$mode" == screen ]]
root=/projectnb/welfgr/cmg-benchmarks
run="$root/runs/$run_id"
sha=$(cat "$run/manifests/source-commit.txt")
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
code="$root/code-b2/$sha"
test "$(cd "$(dirname "$0")/../.." && pwd)" = "$code"
test "$(sha256sum "$root/source-archives/$sha.tar" | cut -d' ' -f1)" = "$(cat "$run/manifests/source-archive-sha256.txt")"
(cd "$code" && sha256sum --status -c "$run/manifests/source-files-sha256.txt")
# An exclusive reservation remains after ambiguous submission or failure.
mkdir "$run/manifests/development-reservation"
job=$(qsub -terse -P welfgr -N cmg-development -pe omp 28 \
    -l h_rt=00:30:00 -l mem_per_core=3G -m n \
    -o "$run/logs/development.stdout" -e "$run/logs/development.stderr" \
    "$code/benchmarks/scc/development.sh" "$run_id" "$mode")
[[ "$job" =~ ^[0-9]+$ ]]
printf 'job_id=%s\nsource=%s\nmode=%s\nslots=28\n' "$job" "$sha" "$mode" > "$run/manifests/submission-development.txt"
printf '%s\n' "$job"
