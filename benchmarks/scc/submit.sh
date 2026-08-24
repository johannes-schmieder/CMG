#!/bin/bash
set -euo pipefail

kind=${1:?usage: submit.sh smoke|main|batch RUN_ID}
run_id=${2:?usage: submit.sh smoke|main|batch RUN_ID}
project_root=/projectnb/welfgr/cmg-benchmarks
case "$kind" in
    smoke)
        task_file="$project_root/code/benchmarks/scc/tasks-smoke.tsv"
        tasks=3
        repetitions=1
        threads='1:32'
        verify=1
        ;;
    main)
        task_file="$project_root/code/benchmarks/scc/tasks-main.tsv"
        tasks=15
        repetitions=3
        threads='1:2:4:8:16:32'
        verify=0
        ;;
    batch)
        task_file="$project_root/code/benchmarks/scc/tasks-batch.tsv"
        tasks=4
        repetitions=3
        threads='1:2:4:8:16:32'
        verify=0
        ;;
    *) echo "unknown submission kind $kind" >&2; exit 2 ;;
esac
job_id=$(qsub -terse -P welfgr -pe omp 32 -binding linear:32 \
    -l cpu_type=Gold-6242 -l mem_per_core=8G -l h_rt=02:00:00 \
    -t "1-$tasks" -tc 2 -N "cmg-$kind" \
    -o "$project_root/runs/$run_id/logs" -e "$project_root/runs/$run_id/logs" \
    -v "CMG_RUN_ID=$run_id,CMG_TASK_FILE=$task_file,CMG_REPETITIONS=$repetitions,CMG_THREAD_SET=$threads,CMG_VERIFY_DETERMINISM=$verify" \
    "$project_root/code/benchmarks/scc/run_array.sh")
base_job_id=${job_id%%.*}
{
    printf 'kind=%s\njob_id=%s\ntask_file=%s\ntasks=%s\nrepetitions=%s\nthreads=%s\n' \
        "$kind" "$job_id" "$task_file" "$tasks" "$repetitions" "$threads"
    qstat -j "$base_job_id"
} > "$project_root/runs/$run_id/manifests/submission-$kind.txt" 2>&1
printf '%s\n' "$job_id"
