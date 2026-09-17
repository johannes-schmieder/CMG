#!/bin/bash
# Freeze one committed reference archive alongside a normal development deploy.
set -euo pipefail
run_id=${1:?usage: deploy_development_screen.sh RUN_ID SOURCE_SHA BASELINE_SHA}
source_sha=${2:?}
baseline_sha=${3:?}
[[ "$baseline_sha" =~ ^[0-9a-f]{40}$ ]]
git cat-file -e "$baseline_sha^{commit}"
git merge-base --is-ancestor "$baseline_sha" "$source_sha"
bash benchmarks/scc/deploy.sh "$run_id" "$source_sha"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
git archive --format=tar --output="$temporary/baseline.tar" "$baseline_sha"
hash=$(shasum -a 256 "$temporary/baseline.tar" | cut -d' ' -f1)
run="/projectnb/welfgr/cmg-benchmarks/runs/$run_id"
ssh scc "mkdir '$run/manifests/screen-reference'"
rsync -a "$temporary/baseline.tar" "scc:$run/manifests/screen-reference/baseline.tar"
ssh scc "test \"\$(sha256sum '$run/manifests/screen-reference/baseline.tar' | cut -d' ' -f1)\" = '$hash' && printf '%s\n' '$baseline_sha' > '$run/manifests/screen-reference/source.txt' && printf '%s\n' '$hash' > '$run/manifests/screen-reference/archive-sha256.txt'"
printf 'CMG_DEVELOPMENT_REFERENCE_SUCCESS source=%s archive=%s\n' "$baseline_sha" "$hash"
