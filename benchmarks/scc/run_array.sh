#!/bin/bash -l
# Run one SGE array task. Resource requests are deliberately kept in submit.sh.
set -euo pipefail

PROJECT_ROOT=/projectnb/welfgr/cmg-benchmarks
CODE_ROOT="$PROJECT_ROOT/code"
RUN_ID=${CMG_RUN_ID:?CMG_RUN_ID is required}
TASK_FILE=${CMG_TASK_FILE:?CMG_TASK_FILE is required}
REPETITIONS=${CMG_REPETITIONS:-3}
TASK_ID=${SGE_TASK_ID:-${CMG_LOCAL_TASK_ID:-1}}
RUN_ROOT="$PROJECT_ROOT/runs/$RUN_ID"
TASK_ROOT="$RUN_ROOT/output/task-$TASK_ID"
RECEIPT_ROOT="$RUN_ROOT/receipts/task-$TASK_ID"
LOG_ROOT="$RUN_ROOT/logs/task-$TASK_ID"
ENVIRONMENT_ID=$(tr -d '\n' < "$RUN_ROOT/manifests/environment-id.txt")
CMG_SOURCE_COMMIT=$(tr -d '\n' < "$RUN_ROOT/manifests/source-commit.txt")
RUST_BINARY="$CODE_ROOT/benchmarks/target/release/scc-benchmark"
C_BINARY="$CODE_ROOT/benchmarks/c-kernel/target/release/cmg-c-kernel-bench"
MATLAB_DRIVER="$CODE_ROOT/benchmarks/matlab"
UPSTREAM_DIR="$PROJECT_ROOT/upstream/cmg-solver-19752fc102f8cae8e34f66457bfaccb1aaa60375/matlab/cmg"

mkdir -p "$TASK_ROOT" "$RECEIPT_ROOT" "$LOG_ROOT"
line=$(sed -n "${TASK_ID}p" "$TASK_FILE")
if [[ -z "$line" ]]; then
    echo "No task $TASK_ID in $TASK_FILE" >&2
    exit 2
fi
IFS=$'\t' read -r FAMILY VERTICES MODE RHS_COUNT <<< "$line"
if [[ -z "$FAMILY" || -z "$VERTICES" || -z "$MODE" || -z "$RHS_COUNT" ]]; then
    echo "Malformed task line: $line" >&2
    exit 2
fi

WORK_ROOT=${TMPDIR:?SGE TMPDIR is required}/cmg-benchmark-task-$TASK_ID
INPUT_ROOT="$WORK_ROOT/input"
WORK_OUTPUT="$WORK_ROOT/output"
mkdir -p "$INPUT_ROOT" "$WORK_OUTPUT"
"$RUST_BINARY" generate "$FAMILY" "$VERTICES" "$RHS_COUNT" "$INPUT_ROOT" \
    > "$LOG_ROOT/generate.log" 2>&1
sha256sum "$INPUT_ROOT"/* > "$RECEIPT_ROOT/input-sha256.txt"

if [[ ${CMG_VERIFY_DETERMINISM:-0} == 1 ]]; then
    SECOND_INPUT="$WORK_ROOT/input-second"
    mkdir -p "$SECOND_INPUT"
    "$RUST_BINARY" generate "$FAMILY" "$VERTICES" "$RHS_COUNT" "$SECOND_INPUT" \
        > "$LOG_ROOT/generate-second.log" 2>&1
    for input_name in graph.bin rhs.bin truth.bin metadata.json; do
        cmp "$INPUT_ROOT/$input_name" "$SECOND_INPUT/$input_name"
    done
    printf 'deterministic=true\n' > "$RECEIPT_ROOT/determinism.txt"
fi

THREAD_SET=${CMG_THREAD_SET:-"1:2:4:8:16:32"}
THREAD_SET=${THREAD_SET//:/ }
read -r -a threads <<< "$THREAD_SET"
if [[ ${#threads[@]} -eq 6 ]]; then
    case $((TASK_ID % 3)) in
        0) threads=(1 2 4 8 16 32) ;;
        1) threads=(4 8 16 32 1 2) ;;
        2) threads=(16 32 1 2 4 8) ;;
    esac
fi

run_rust() {
    local thread_count=$1
    local stem="rust-t${thread_count}"
    local raw="$WORK_OUTPUT/$stem.json"
    local time_file="$WORK_OUTPUT/$stem.time"
    /usr/bin/time -v -o "$time_file" "$RUST_BINARY" run "$INPUT_ROOT" \
        "$thread_count" "$REPETITIONS" "$MODE" "$raw" \
        > "$LOG_ROOT/$stem.log" 2>&1
    python3 "$CODE_ROOT/benchmarks/scc/enrich_result.py" "$raw" "$time_file" \
        "$INPUT_ROOT" "$RUN_ID" "$TASK_ID" "$ENVIRONMENT_ID"
    python3 -m json.tool "$raw" > /dev/null
    cp "$raw" "$TASK_ROOT/$stem.json"
    cp "$time_file" "$RECEIPT_ROOT/$stem.time"
}

run_matlab() {
    local thread_count=$1
    local stem="matlab-t${thread_count}"
    local raw="$WORK_OUTPUT/$stem.json"
    local time_file="$WORK_OUTPUT/$stem.time"
    export CMG_INPUT_DIR="$INPUT_ROOT"
    export CMG_THREADS="$thread_count"
    export CMG_REPETITIONS="$REPETITIONS"
    export CMG_MODE="$MODE"
    export CMG_OUTPUT_FILE="$raw"
    export CMG_UPSTREAM_DIR="$UPSTREAM_DIR"
    export CMG_SOURCE_COMMIT
    /usr/bin/time -v -o "$time_file" matlab -batch \
        "addpath('$MATLAB_DRIVER'); scc_benchmark_from_env" \
        > "$LOG_ROOT/$stem.log" 2>&1
    python3 "$CODE_ROOT/benchmarks/scc/enrich_result.py" "$raw" "$time_file" \
        "$INPUT_ROOT" "$RUN_ID" "$TASK_ID" "$ENVIRONMENT_ID"
    python3 -m json.tool "$raw" > /dev/null
    cp "$raw" "$TASK_ROOT/$stem.json"
    cp "$time_file" "$RECEIPT_ROOT/$stem.time"
}

module purge
module load "$(tr -d '\n' < "$PROJECT_ROOT/toolchains/matlab-module.txt")"
module load python3/3.12.4
export OMP_PROC_BIND=close
export OMP_PLACES=cores
export RAYON_NUM_THREADS=32

for index in "${!threads[@]}"; do
    thread_count=${threads[$index]}
    if (( (TASK_ID + index) % 2 == 0 )); then
        run_rust "$thread_count"
        run_matlab "$thread_count"
    else
        run_matlab "$thread_count"
        run_rust "$thread_count"
    fi
done

if [[ "$MODE" == single && ( "$FAMILY" == path || "$FAMILY" == worker-firm ) ]]; then
    c_raw="$WORK_OUTPUT/c-kernel.json"
    c_time="$WORK_OUTPUT/c-kernel.time"
    /usr/bin/time -v -o "$c_time" "$C_BINARY" --case "$FAMILY" \
        --vertices "$VERTICES" --repetitions "$REPETITIONS" --output "$c_raw" \
        > "$LOG_ROOT/c-kernel.log" 2>&1
    python3 -m json.tool "$c_raw" > /dev/null
    cp "$c_raw" "$TASK_ROOT/c-kernel.json"
    cp "$c_time" "$RECEIPT_ROOT/c-kernel.time"
fi

python3 "$CODE_ROOT/benchmarks/scc/validate_task.py" \
    "$TASK_ROOT" "$RECEIPT_ROOT" "$FAMILY" "$VERTICES" "$MODE" "$THREAD_SET"
printf 'success=true\nfamily=%s\nvertices=%s\nmode=%s\nhost=%s\n' \
    "$FAMILY" "$VERTICES" "$MODE" "$(hostname)" > "$RECEIPT_ROOT/SUCCESS"
echo "CMG_BENCH_TASK_SUCCESS task=$TASK_ID family=$FAMILY vertices=$VERTICES mode=$MODE"
