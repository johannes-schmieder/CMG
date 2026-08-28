#!/bin/bash -l
# Build and fingerprint only the pinned Rust/C comparison harness.
set -euo pipefail

project_root=/projectnb/welfgr/cmg-benchmarks
run_id=${1:?usage: bootstrap_c_kernel.sh RUN_ID}
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
archive_sha=$(tr -d '\n' < "$run_root/manifests/source-archive-sha256.txt")
code_root="$project_root/code-b2/$source_sha"
test "$(sha256sum "$project_root/source-archives/$source_sha.tar" | cut -d' ' -f1)" = "$archive_sha"

export RUSTUP_HOME="$project_root/toolchains/rustup"
export CARGO_HOME="$project_root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
rustup toolchain install 1.98.0 --profile minimal
rustup default 1.98.0
rustup component add rustfmt clippy --toolchain 1.98.0

export CMG_BENCH_COMMIT="$source_sha"
export CMG_BENCH_ARCHIVE_SHA256="$archive_sha"
cd "$code_root"
cargo fmt --all --check --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$run_root/logs/cargo-c-kernel-fmt.log" 2>&1
cargo clippy --locked --all-targets --manifest-path benchmarks/c-kernel/Cargo.toml \
    -- -D warnings > "$run_root/logs/cargo-c-kernel-clippy.log" 2>&1
cargo test --release --locked --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$run_root/logs/cargo-c-kernel-test.log" 2>&1
cargo build --release --locked -vv --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$run_root/logs/cargo-c-kernel-build.log" 2>&1

binary="$code_root/benchmarks/c-kernel/target/release/cmg-c-kernel-bench"
"$binary" identity "$run_root/manifests/c-kernel-identity.json" \
    > "$run_root/logs/c-kernel-identity.log" 2>&1
ldd "$binary" > "$run_root/manifests/c-kernel-linked-libraries.txt"
python3 - "$run_root/manifests/c-kernel-identity.json" "$source_sha" "$archive_sha" <<'PY'
import json
import sys

path, source, archive = sys.argv[1:]
value = json.load(open(path))
assert value["source_commit"] == source
assert value["source_archive_sha256"] == archive
assert value["protocol_version"] == "cmg-scc2-v1"
assert value["upstream_commit"] == "19752fc102f8cae8e34f66457bfaccb1aaa60375"
assert len(value["binary_sha256"]) == 64
PY

{
    hostname
    uname -a
    lscpu
    rustc --version --verbose
    cargo --version --verbose
} > "$run_root/manifests/c-kernel-build-environment.txt"
sha256sum Cargo.lock benchmarks/c-kernel/Cargo.lock \
    "$run_root/manifests/c-kernel-identity.json" \
    "$run_root/manifests/c-kernel-build-environment.txt" \
    | sha256sum | cut -d' ' -f1 > "$run_root/manifests/environment-id.txt"
printf 'success=true\nsource_commit=%s\nsource_archive_sha256=%s\n' \
    "$source_sha" "$archive_sha" > "$run_root/receipts/C_KERNEL_BUILD_SUCCESS"
echo "CMG_SCC2_C_KERNEL_BOOTSTRAP_SUCCESS run=$run_id"
