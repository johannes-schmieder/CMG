#!/bin/bash -l
set -euo pipefail
test "${NSLOTS:-0}" = 28
test -n "${JOB_ID:-}"
root=/projectnb/welfgr/cmg-benchmarks
run_id=${1:?}
mode=${2:?}
[[ "$run_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-b2v1-development$ ]]
[[ "$mode" == shutdown || "$mode" == integration || "$mode" == screen || "$mode" == oracle ]]
run="$root/runs/$run_id"
mkdir "$run/work/development-started"
sha=$(cat "$run/manifests/source-commit.txt")
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
code="$root/code-b2/$sha"
cd "$code"
sha256sum --status -c "$run/manifests/source-files-sha256.txt"
test "$(sha256sum "$root/source-archives/$sha.tar" | cut -d' ' -f1)" = "$(cat "$run/manifests/source-archive-sha256.txt")"
export RUSTUP_HOME="$root/toolchains/rustup" CARGO_HOME="$root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH" RUSTUP_TOOLCHAIN=1.98.0
export CARGO_TARGET_DIR="$run/work/target" CARGO_BUILD_JOBS=4 RUST_TEST_THREADS=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
{
    hostname
    uname -a
    lscpu
    taskset -pc $$
    printf 'job=%s slots=%s build_jobs=4 test_threads=1 maximum_test_pool=8\n' "$JOB_ID" "$NSLOTS"
    rustc -Vv
    cargo -Vv
} > "$run/manifests/development-environment.txt"
step() {
    local name=$1
    shift
    "$@" > "$run/logs/$name.log" 2>&1
    printf 'CMG_DEVELOPMENT_STEP_SUCCESS %s\n' "$name"
}
step fmt cargo fmt --all -- --check
step clippy cargo clippy --locked --offline --all-features --all-targets -- -D warnings
if [[ "$mode" == oracle ]]; then
    step oracle-diagnosis bash "$code/benchmarks/scc/bridge_oracle.sh" "$run"
    sha256sum --status -c "$run/manifests/source-files-sha256.txt"
    printf 'source=%s\njob=%s\nmode=%s\n' "$sha" "$JOB_ID" "$mode" > "$run/receipts/ORACLE_DIAGNOSIS_COMPLETE"
    printf 'CMG_DEVELOPMENT_DIAGNOSIS_COMPLETE source=%s mode=%s\n' "$sha" "$mode"
    exit 0
elif [[ "$mode" == shutdown ]]; then
    step shutdown cargo test --locked --offline --features parallel --lib execution::lifetime_tests -- --nocapture
elif [[ "$mode" == screen ]]; then
    step final-memory cargo test --locked --offline --test storage_accounting --all-features
    step final-memory-minimal cargo test --locked --offline --test storage_accounting
    step screen bash "$code/benchmarks/scc/ownership_screen.sh" "$run"
else
    step focused cargo test --locked --offline --all-features --test storage_accounting --test component_executor -- --nocapture
    step minimal cargo test --locked --offline --all-targets
    step parallel cargo test --locked --offline --all-targets --features parallel -- --skip custom_executor_supports_up_to_thirty_two_threads
    step components cargo test --locked --offline --all-targets --features experimental-components
    step all-features cargo test --locked --offline --all-targets --all-features -- --skip custom_executor_supports_up_to_thirty_two_threads
    step release cargo test --locked --offline --release --all-targets --all-features -- --skip custom_executor_supports_up_to_thirty_two_threads
    step docs env RUSTDOCFLAGS=-Dwarnings cargo doc --locked --offline --no-deps --all-features
    step doctests cargo test --locked --offline --doc --all-features
    step msrv cargo +1.85.1 check --locked --offline --all-features --all-targets
fi
find "$CARGO_TARGET_DIR" -type f -perm -0100 -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/development-artifacts.sha256"
sha256sum --status -c "$run/manifests/source-files-sha256.txt"
printf 'source=%s\njob=%s\nmode=%s\n' "$sha" "$JOB_ID" "$mode" > "$run/receipts/DEVELOPMENT_SUCCESS"
printf 'CMG_DEVELOPMENT_SUCCESS source=%s mode=%s\n' "$sha" "$mode"
