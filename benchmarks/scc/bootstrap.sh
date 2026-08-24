#!/bin/bash -l
# Build the frozen source tree and unmodified upstream MEX extension on SCC.
set -euo pipefail

PROJECT_ROOT=/projectnb/welfgr/cmg-benchmarks
CODE_ROOT="$PROJECT_ROOT/code"
RUN_ID=${1:?usage: bootstrap.sh RUN_ID}
RUN_ROOT="$PROJECT_ROOT/runs/$RUN_ID"
RUSTUP_HOME="$PROJECT_ROOT/toolchains/rustup"
CARGO_HOME="$PROJECT_ROOT/toolchains/cargo"
export RUSTUP_HOME CARGO_HOME
export PATH="$CARGO_HOME/bin:$PATH"
mkdir -p "$PROJECT_ROOT/upstream" "$PROJECT_ROOT/toolchains" \
    "$RUN_ROOT/manifests" "$RUN_ROOT/logs" "$RUN_ROOT/work" \
    "$RUN_ROOT/output" "$RUN_ROOT/receipts"

if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
    sh "$PROJECT_ROOT/toolchains/rustup-init" -y --profile minimal \
        --default-toolchain 1.98.0 --no-modify-path
else
    rustup toolchain install 1.98.0 --profile minimal
    rustup default 1.98.0
fi
rustc --version --verbose > "$RUN_ROOT/manifests/rustc.txt"
cargo --version --verbose > "$RUN_ROOT/manifests/cargo.txt"
rustup component add rustfmt clippy --toolchain 1.98.0

CMG_SOURCE_COMMIT=$(tr -d '\n' < "$RUN_ROOT/manifests/source-commit.txt")
CMG_BENCH_COMMIT=$CMG_SOURCE_COMMIT
export CMG_SOURCE_COMMIT CMG_BENCH_COMMIT
cd "$CODE_ROOT"
cargo build --release --locked --all-features \
    > "$RUN_ROOT/logs/cargo-root-build.log" 2>&1
cargo build --release --locked --all-features --all-targets \
    --manifest-path benchmarks/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-benchmarks-build.log" 2>&1
cargo build --release --locked --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-c-kernel-build.log" 2>&1
cargo test --locked --all-features \
    > "$RUN_ROOT/logs/cargo-root-test.log" 2>&1
cargo test --locked --all-features --manifest-path benchmarks/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-benchmarks-test.log" 2>&1
cargo test --release --locked --all-features \
    > "$RUN_ROOT/logs/cargo-root-release-test.log" 2>&1
cargo test --release --locked --all-features --manifest-path benchmarks/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-benchmarks-release-test.log" 2>&1
cargo test --locked --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-c-kernel-test.log" 2>&1
cargo fmt --all --check > "$RUN_ROOT/logs/cargo-fmt.log" 2>&1
cargo fmt --all --check --manifest-path benchmarks/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-benchmarks-fmt.log" 2>&1
cargo fmt --all --check --manifest-path benchmarks/c-kernel/Cargo.toml \
    > "$RUN_ROOT/logs/cargo-c-kernel-fmt.log" 2>&1
cargo clippy --locked --all-features --all-targets -- -D warnings \
    > "$RUN_ROOT/logs/cargo-clippy.log" 2>&1
cargo clippy --locked --all-features --all-targets --manifest-path benchmarks/Cargo.toml \
    -- -D warnings > "$RUN_ROOT/logs/cargo-benchmarks-clippy.log" 2>&1

module purge
selected_matlab=''
for candidate in matlab/2026a matlab/2025b matlab/2024b; do
    module purge
    if module load "$candidate" >/dev/null 2>&1; then
        if matlab -batch "addpath('$CODE_ROOT/benchmarks/matlab'); build_upstream('$PROJECT_ROOT/upstream/cmg-solver-19752fc102f8cae8e34f66457bfaccb1aaa60375/matlab/cmg','$RUN_ROOT/manifests/mex-build.json')" \
            > "$RUN_ROOT/logs/mex-build-${candidate#*/}.log" 2>&1; then
            selected_matlab=$candidate
            break
        fi
    fi
done
if [[ -z "$selected_matlab" ]]; then
    echo 'All MATLAB MEX builds failed' >&2
    exit 1
fi
printf '%s\n' "$selected_matlab" > "$PROJECT_ROOT/toolchains/matlab-module.txt"

{
    hostname
    uname -a
    lscpu
    module list 2>&1
} > "$RUN_ROOT/manifests/build-environment.txt"
sha256sum Cargo.lock benchmarks/Cargo.lock benchmarks/c-kernel/Cargo.lock \
    > "$RUN_ROOT/manifests/cargo-lock-sha256.txt"
find . -type f -not -path './target/*' -not -path './benchmarks/target/*' \
    -not -path './benchmarks/c-kernel/target/*' -print0 | sort -z | xargs -0 sha256sum \
    > "$RUN_ROOT/manifests/source-files-sha256.txt"
sha256sum "$RUN_ROOT/manifests/source-files-sha256.txt" "$RUN_ROOT/manifests/rustc.txt" \
    "$RUN_ROOT/manifests/mex-build.json" | sha256sum | cut -d' ' -f1 \
    > "$RUN_ROOT/manifests/environment-id.txt"
printf 'success=true\nmatlab_module=%s\n' "$selected_matlab" \
    > "$RUN_ROOT/receipts/BUILD_SUCCESS"
echo "CMG_BOOTSTRAP_SUCCESS run=$RUN_ID matlab=$selected_matlab"
