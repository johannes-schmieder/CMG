#!/bin/bash
# Called only within the bounded compute-node development allocation.
set -euo pipefail
test "${NSLOTS:-0}" = 28
test -n "${JOB_ID:-}"
run=${1:?}
code=$(pwd)
test "$(sha256sum "$run/manifests/screen-reference/baseline.tar" | cut -d' ' -f1)" = "$(cat "$run/manifests/screen-reference/archive-sha256.txt")"
mkdir "$run/work/screen" "$run/work/screen/baseline-source" "$run/output/screen"
tar -xf "$run/manifests/screen-reference/baseline.tar" -C "$run/work/screen/baseline-source"
sha256sum "$code/benchmarks/src/bin/ownership-screen.rs" > "$run/manifests/screen-harness.sha256"
for variant in baseline candidate; do
    driver="$run/work/screen/$variant"
    mkdir "$driver" "$driver/src" "$run/output/screen/inputs-$variant"
    cp "$code/benchmarks/src/bin/ownership-screen.rs" "$driver/src/main.rs"
    source_path="$code"
    if [[ "$variant" == baseline ]]; then source_path="$run/work/screen/baseline-source"; fi
    printf '[package]\nname="ownership-screen"\nversion="0.0.0"\nedition="2024"\n[dependencies]\ncmg={path="%s",features=["parallel","experimental-components"]}\n[features]\ncandidate-screen=[]\nscreen-allocations=[]\n' "$source_path" > "$driver/Cargo.toml"
    features=screen-allocations
    if [[ "$variant" == candidate ]]; then features=candidate-screen,screen-allocations; fi
    cargo build --offline --release --manifest-path "$driver/Cargo.toml" --features "$features" --target-dir "$driver/alloc-target" > "$run/logs/screen-build-$variant-alloc.log" 2>&1
    "$driver/alloc-target/release/ownership-screen" "$run/output/screen/inputs-$variant" > "$run/output/screen/$variant-alloc.jsonl"
    features=''
    if [[ "$variant" == candidate ]]; then features=candidate-screen; fi
    cargo build --locked --offline --release --manifest-path "$driver/Cargo.toml" --features "$features" --target-dir "$driver/time-target" > "$run/logs/screen-build-$variant-time.log" 2>&1
    "$driver/time-target/release/ownership-screen" > "$run/output/screen/$variant-warmup.jsonl"
done
for case in path mixed dense weak fallback; do
    cmp "$run/output/screen/inputs-baseline/$case.bin" "$run/output/screen/inputs-candidate/$case.bin"
done
for round in 1 2 3 4 5 6 7; do
    variants='baseline candidate'
    if (( round % 2 == 0 )); then variants='candidate baseline'; fi
    for variant in $variants; do
        "$run/work/screen/$variant/time-target/release/ownership-screen" > "$run/output/screen/$variant-$round.jsonl"
    done
done
find "$run/work/screen" -type f \( -name ownership-screen -o -name Cargo.lock \) -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/screen-builds.sha256"
find "$run/output/screen" -type f -print0 | sort -z | xargs -0 sha256sum > "$run/manifests/screen-results.sha256"
python3 "$code/benchmarks/scc/validate_ownership_screen.py" "$run"
printf 'CMG_OWNERSHIP_SCREEN_SUCCESS\n'
