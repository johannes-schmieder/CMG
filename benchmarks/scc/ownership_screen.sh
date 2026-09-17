#!/bin/bash
# Called only within the bounded compute-node development allocation.
set -euo pipefail
test "${NSLOTS:-0}" = 28
test -n "${JOB_ID:-}"
run=${1:?}
lane=${2:-screen}
[[ "$lane" == screen || "$lane" == screen-admitted ]]
work="$run/work/$lane"
output="$run/output/$lane"
code=$(pwd)
test "$(sha256sum "$run/manifests/screen-reference/baseline.tar" | cut -d' ' -f1)" = "$(cat "$run/manifests/screen-reference/archive-sha256.txt")"
mkdir "$work" "$work/baseline-source" "$output"
tar -xf "$run/manifests/screen-reference/baseline.tar" -C "$work/baseline-source"
sha256sum "$code/benchmarks/src/bin/ownership-screen.rs" > "$run/manifests/$lane-harness.sha256"
for variant in baseline candidate; do
    driver="$work/$variant"
    mkdir "$driver" "$driver/src" "$output/inputs-$variant"
    cp "$code/benchmarks/src/bin/ownership-screen.rs" "$driver/src/main.rs"
    source_path="$code"
    if [[ "$variant" == baseline ]]; then source_path="$work/baseline-source"; fi
    printf '[package]\nname="ownership-screen"\nversion="0.0.0"\nedition="2024"\n[dependencies]\ncmg={path="%s",features=["parallel","experimental-components"]}\n[features]\ncandidate-screen=[]\nscreen-allocations=[]\nserial-setup-screen=[]\nadmitted-screen=["candidate-screen","serial-setup-screen"]\n' "$source_path" > "$driver/Cargo.toml"
    features=''
    if [[ "$variant" == candidate ]]; then features=candidate-screen; fi
    if [[ "$lane" == screen-admitted ]]; then
        features=serial-setup-screen
        if [[ "$variant" == candidate ]]; then features=admitted-screen; fi
    fi
    cargo build --offline --release --manifest-path "$driver/Cargo.toml" --features "${features:+$features,}screen-allocations" --target-dir "$driver/alloc-target" > "$run/logs/$lane-build-$variant-alloc.log" 2>&1
    "$driver/alloc-target/release/ownership-screen" "$output/inputs-$variant" > "$output/$variant-alloc.jsonl"
    cargo build --locked --offline --release --manifest-path "$driver/Cargo.toml" --features "$features" --target-dir "$driver/time-target" > "$run/logs/$lane-build-$variant-time.log" 2>&1
    "$driver/time-target/release/ownership-screen" > "$output/$variant-warmup.jsonl"
done
for case in path mixed dense weak fallback; do
    cmp "$output/inputs-baseline/$case.bin" "$output/inputs-candidate/$case.bin"
done
for round in 1 2 3 4 5 6 7; do
    variants='baseline candidate'
    if (( round % 2 == 0 )); then variants='candidate baseline'; fi
    for variant in $variants; do
        "$work/$variant/time-target/release/ownership-screen" > "$output/$variant-$round.jsonl"
    done
done
find "$work" -type f \( -name ownership-screen -o -name Cargo.lock \) -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/$lane-builds.sha256"
find "$output" -type f -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/$lane-results.sha256"
python3 "$code/benchmarks/scc/validate_ownership_screen.py" "$run" "$lane"
printf 'CMG_OWNERSHIP_SCREEN_SUCCESS lane=%s\n' "$lane"
