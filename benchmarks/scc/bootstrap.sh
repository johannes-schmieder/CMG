#!/bin/bash -l
# Verify, build, and fingerprint one immutable SCC2 source snapshot on SCC.
set -euo pipefail

project_root=/projectnb/welfgr/cmg-benchmarks
run_id=${1:?usage: bootstrap.sh RUN_ID}
run_root="$project_root/runs/$run_id"
source_sha=$(tr -d '\n' < "$run_root/manifests/source-commit.txt")
archive_sha=$(tr -d '\n' < "$run_root/manifests/source-archive-sha256.txt")
code_root="$project_root/code-b2/$source_sha"
test "$(sha256sum "$project_root/source-archives/$source_sha.tar" | cut -d' ' -f1)" = "$archive_sha"

if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile
    set -u
fi
type module >/dev/null 2>&1

export RUSTUP_HOME="$project_root/toolchains/rustup"
export CARGO_HOME="$project_root/toolchains/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
rustup_log="$run_root/logs/rustup.log"
if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
    "$project_root/toolchains/rustup-init" -y --profile minimal --default-toolchain 1.98.0 --no-modify-path \
        > "$rustup_log" 2>&1
else
    rustup toolchain install 1.98.0 --profile minimal > "$rustup_log" 2>&1
fi
rustup default 1.98.0 >> "$rustup_log" 2>&1
rustup component add rustfmt clippy --toolchain 1.98.0 >> "$rustup_log" 2>&1
rustc --version --verbose > "$run_root/manifests/rustc.txt"
cargo --version --verbose > "$run_root/manifests/cargo.txt"

export CMG_BENCH_COMMIT="$source_sha"
export CMG_BENCH_ARCHIVE_SHA256="$archive_sha"
cd "$code_root"
cargo fmt --all --check > "$run_root/logs/cargo-fmt.log" 2>&1
cargo fmt --all --check --manifest-path benchmarks/Cargo.toml > "$run_root/logs/cargo-bench-fmt.log" 2>&1
cargo clippy --locked --all-features --all-targets -- -D warnings > "$run_root/logs/cargo-clippy.log" 2>&1
cargo clippy --locked --all-features --all-targets --manifest-path benchmarks/Cargo.toml -- -D warnings > "$run_root/logs/cargo-bench-clippy.log" 2>&1
cargo test --locked --all-features > "$run_root/logs/cargo-test.log" 2>&1
cargo test --release --locked --all-features > "$run_root/logs/cargo-release-test.log" 2>&1
cargo test --locked --all-features --manifest-path benchmarks/Cargo.toml > "$run_root/logs/cargo-bench-test.log" 2>&1
cargo build --release --locked --all-features > "$run_root/logs/cargo-build.log" 2>&1
cargo build --release --locked --all-features --all-targets --manifest-path benchmarks/Cargo.toml > "$run_root/logs/cargo-bench-build.log" 2>&1
CARGO_TARGET_DIR=benchmarks/target-cascadelake RUSTFLAGS='-C target-cpu=cascadelake' \
    cargo build --release --locked --manifest-path benchmarks/Cargo.toml --bin fused-rhs-experiment \
    > "$run_root/logs/cargo-fused-cascadelake-build.log" 2>&1
cargo fmt --all --check --manifest-path benchmarks/c-kernel/Cargo.toml > "$run_root/logs/cargo-c-kernel-fmt.log" 2>&1
cargo clippy --locked --all-targets --manifest-path benchmarks/c-kernel/Cargo.toml -- -D warnings > "$run_root/logs/cargo-c-kernel-clippy.log" 2>&1
cargo test --locked --manifest-path benchmarks/c-kernel/Cargo.toml > "$run_root/logs/cargo-c-kernel-test.log" 2>&1
cargo build --release --locked -vv --manifest-path benchmarks/c-kernel/Cargo.toml > "$run_root/logs/cargo-c-kernel-build.log" 2>&1
find "$code_root/benchmarks/target/release" -maxdepth 1 -type f -perm -0100 -print0 \
    | sort -z | xargs -0 sha256sum > "$run_root/manifests/benchmark-binaries-sha256.txt"
sha256sum "$code_root/benchmarks/target/release/fused-rhs-experiment" | cut -d' ' -f1 \
    > "$run_root/manifests/fused-portable-binary-sha256.txt"
sha256sum "$code_root/benchmarks/target-cascadelake/release/fused-rhs-experiment" | cut -d' ' -f1 \
    > "$run_root/manifests/fused-cascadelake-binary-sha256.txt"

diagnostics="$code_root/benchmarks/target/release/scc2-diagnostics"
"$diagnostics" identity "$run_root/manifests/rust-identity.json" > "$run_root/logs/rust-identity.log" 2>&1
sha256sum "$code_root/benchmarks/target/release/scc2-memory" | cut -d' ' -f1 \
    > "$run_root/manifests/memory-binary-sha256.txt"
c_kernel="$code_root/benchmarks/c-kernel/target/release/cmg-c-kernel-bench"
"$c_kernel" identity "$run_root/manifests/c-kernel-identity.json" > "$run_root/logs/c-kernel-identity.log" 2>&1
ldd "$c_kernel" > "$run_root/manifests/c-kernel-linked-libraries.txt"
python3 - "$run_root/manifests/rust-identity.json" "$run_root/manifests/c-kernel-identity.json" "$source_sha" "$archive_sha" <<'PY'
import json
import sys
rust_path, c_path, source, archive = sys.argv[1:]
for path in (rust_path, c_path):
    value = json.load(open(path))
    assert value["source_commit"] == source and value["source_commit"] != "unknown"
    assert value["source_archive_sha256"] == archive and value["source_archive_sha256"] != "unknown"
    assert value["protocol_version"] == "cmg-scc2-v1"
    assert len(value["binary_sha256"]) == 64
PY

module purge
selected_matlab=''
upstream="$project_root/upstream/cmg-solver-19752fc102f8cae8e34f66457bfaccb1aaa60375/matlab/cmg"
for candidate in matlab/2026a matlab/2025b matlab/2024b; do
    module purge
    if module load "$candidate" >/dev/null 2>&1; then
        if matlab -batch "addpath('$code_root/benchmarks/matlab'); build_upstream('$upstream','$run_root/manifests/mex-build.json')" > "$run_root/logs/mex-build-${candidate#*/}.log" 2>&1; then
            selected_matlab=$candidate
            break
        fi
    fi
done
test -n "$selected_matlab"
printf '%s\n' "$selected_matlab" > "$project_root/toolchains/matlab-module.txt"
printf '%s\n' "$selected_matlab" > "$run_root/manifests/matlab-module.txt"
matlab -batch "addpath('$upstream'); addpath(fullfile('$upstream','mex')); assert(exist('mx_preconditioner_','file') == 3); assert(exist('mx_splitforest_','file') == 3)" \
    > "$run_root/logs/mex-visibility.log" 2>&1
find "$upstream" -type f -name '*.mexa64' -print0 | sort -z | xargs -0 sha256sum > "$run_root/manifests/mex-files-sha256.txt"
sha256sum "$run_root/manifests/mex-files-sha256.txt" | cut -d' ' -f1 > "$run_root/manifests/mex-binary-sha256.txt"
while read -r _ mex_binary; do
    printf 'binary=%s\n' "$mex_binary"
    ldd "$mex_binary"
done < "$run_root/manifests/mex-files-sha256.txt" > "$run_root/manifests/mex-linked-libraries.txt"

module purge
module load python3/3.12.4
export PYTHONPYCACHEPREFIX="$run_root/work/python-cache"
python3 -m compileall -q "$code_root/benchmarks/scc" \
    > "$run_root/logs/python-compileall.log" 2>&1
python3 -m unittest discover -s "$code_root/benchmarks/scc/tests" -v \
    > "$run_root/logs/python-tests.log" 2>&1
for kind in smoke baseline routing reuse numa memory accuracy batch matched-edge; do
    python3 - "$run_root/manifests/tasks/$kind.jsonl" "$code_root/benchmarks/scc/schemas/task.schema.json" <<'PY'
import json
import sys
import jsonschema
manifest, schema = sys.argv[1:]
validator = jsonschema.Draft202012Validator(json.load(open(schema)))
for number, line in enumerate(open(manifest), 1):
    validator.validate(json.loads(line))
PY
done
for manifest in "$run_root"/manifests/tasks/fused*.jsonl; do
    python3 "$code_root/benchmarks/scc/validate_fused_manifest.py" "$manifest" \
        >> "$run_root/logs/fused-manifests.log" 2>&1
done
python3 "$code_root/benchmarks/scc/tasks/generate_tasks.py" smoke \
    "$run_root/work/tasks-smoke-roundtrip.jsonl" > "$run_root/logs/task-generator.log" 2>&1
cmp "$run_root/work/tasks-smoke-roundtrip.jsonl" "$run_root/manifests/tasks/smoke.jsonl"
cpu_profiles=$(python3 -c 'import json,sys; print(" ".join(sorted(json.load(open(sys.argv[1])))))' \
    "$code_root/benchmarks/scc/fused_cpu_profiles.json")
for cpu_profile in $cpu_profiles; do
    for kind in fused-cpu-smoke fused-cpu-screen; do
        experiment="$kind-$cpu_profile"
        roundtrip="$run_root/work/tasks-$experiment-roundtrip.jsonl"
        python3 "$code_root/benchmarks/scc/tasks/generate_tasks.py" "$kind" "$roundtrip" \
            --cpu-profile "$cpu_profile" >> "$run_root/logs/task-generator.log" 2>&1
        cmp "$roundtrip" "$run_root/manifests/tasks/$experiment.jsonl"
    done
done

{
    hostname
    uname -a
    lscpu
    module list 2>&1
} > "$run_root/manifests/build-environment.txt"
sha256sum Cargo.lock benchmarks/Cargo.lock benchmarks/c-kernel/Cargo.lock \
    "$run_root/manifests/rust-identity.json" "$run_root/manifests/c-kernel-identity.json" \
    "$run_root/manifests/mex-files-sha256.txt" "$run_root/manifests/build-environment.txt" \
    | sha256sum | cut -d' ' -f1 > "$run_root/manifests/environment-id.txt"
printf 'success=true\nsource_commit=%s\nsource_archive_sha256=%s\nmatlab_module=%s\n' \
    "$source_sha" "$archive_sha" "$selected_matlab" > "$run_root/receipts/BUILD_SUCCESS"
echo "CMG_SCC2_BOOTSTRAP_SUCCESS run=$run_id matlab=$selected_matlab"
