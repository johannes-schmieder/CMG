#!/bin/bash -l
# Run the complete bounded pinned-C comparison on one isolated SCC node.
set -euo pipefail

project_root=/projectnb/welfgr/cmg-benchmarks
run_id=${CMG_RUN_ID:?CMG_RUN_ID is required}
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
archive_sha=$(tr -d '\n' < "$run_root/manifests/source-archive-sha256.txt")
code_root="$project_root/code-b2/$source_sha"
binary="$code_root/benchmarks/c-kernel/target/release/cmg-c-kernel-bench"
output_root="$run_root/output/c-kernel"
receipt_root="$run_root/receipts/c-kernel"
log_root="$run_root/logs/c-kernel"
repetitions=${CMG_REPETITIONS:-7}

test -f "$run_root/receipts/C_KERNEL_BUILD_SUCCESS"
mkdir -p "$output_root" "$receipt_root" "$log_root"
module purge
export RUSTUP_HOME="$project_root/toolchains/rustup"
export CARGO_HOME="$project_root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

{
    printf 'run_id=%s\nsource_commit=%s\nsource_archive_sha256=%s\n' \
        "$run_id" "$source_sha" "$archive_sha"
    printf 'job_id=%s\nhost=%s\nslots=%s\nrepetitions=%s\n' \
        "${JOB_ID:-}" "$(hostname)" "${NSLOTS:-}" "$repetitions"
    lscpu
    taskset -pc $$
} > "$receipt_root/runtime-environment.txt"

for specification in \
    'path 100000' \
    'worker-firm 100000' \
    'worker-firm 300000' \
    'path 300000' \
    'path 1000000' \
    'worker-firm 1000000'
do
    read -r case_name vertices <<< "$specification"
    stem="$case_name-$vertices"
    /usr/bin/time -v -o "$receipt_root/$stem.time" \
        "$binary" --case "$case_name" --vertices "$vertices" \
        --repetitions "$repetitions" --output "$output_root/$stem.json" \
        > "$log_root/$stem.log" 2>&1
done

python3 - "$output_root" "$run_root/manifests/c-kernel-identity.json" \
    "$source_sha" "$archive_sha" "$repetitions" <<'PY'
import json
import math
import sys
from pathlib import Path

output_root, identity_path, source, archive, repetitions = sys.argv[1:]
output_root = Path(output_root)
identity = json.load(open(identity_path))
expected = {
    ("path", 100_000), ("path", 300_000), ("path", 1_000_000),
    ("worker-firm", 100_000), ("worker-firm", 300_000),
    ("worker-firm", 1_000_000),
}
seen = set()
for path in sorted(output_root.glob("*.json")):
    value = json.load(open(path))
    key = (value["case"], int(value["vertices"]))
    assert key in expected and key not in seen
    seen.add(key)
    assert value["schema"] == 3
    assert value["protocol_version"] == "cmg-scc2-v1"
    assert value["source_commit"] == source
    assert value["source_archive_sha256"] == archive
    assert value["binary_sha256"] == identity["binary_sha256"]
    assert value["upstream_commit"] == "19752fc102f8cae8e34f66457bfaccb1aaa60375"
    assert int(value["repetitions"]) == int(repetitions)
    ratios = [
        value["rust_over_c"],
        value["projection"]["restriction_rust_over_c"],
        value["projection"]["prolongation_rust_over_c"],
        value["cycle"]["rust_over_c"],
    ]
    assert all(math.isfinite(item) and item > 0 for item in ratios)
    assert value["max_scaled_error"] <= 2e-12
    assert value["projection"]["restriction_max_scaled_error"] <= 2e-12
    assert value["projection"]["prolongation_max_scaled_error"] <= 2e-12
    assert value["cycle"]["quotient_max_scaled_error"] <= 5e-10
assert seen == expected
PY

sha256sum "$output_root"/*.json > "$receipt_root/results-sha256.txt"
printf 'success=true\nsource_commit=%s\nsource_archive_sha256=%s\ncases=6\n' \
    "$source_sha" "$archive_sha" > "$receipt_root/SUCCESS"
echo "CMG_SCC2_C_KERNEL_SUCCESS run=$run_id cases=6"
