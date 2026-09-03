#!/usr/bin/env python3
"""Generate frozen JSONL task manifests for each SCC2 experiment."""

from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import FAMILIES, PROTOCOL_VERSION, THREADS, write_jsonl  # noqa: E402


def base(experiment: str, family: str, vertices: int) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment": experiment,
        "family": family,
        "vertices": vertices,
        "tolerance": 1.0e-8,
        "max_iterations": 1000,
    }


def tasks(kind: str, optimal: dict[str, int]) -> list[dict]:
    rows: list[dict] = []
    if kind == "smoke":
        for family in ("path", "dense-worker-firm"):
            row = base(kind, family, 100_000)
            row.update(implementations=["rust", "matlab"], threads=[1, 32], rhs_count=1, warmups=1, repetitions=1)
            rows.append(row)
    elif kind == "baseline":
        for family in FAMILIES:
            row = base(kind, family, 1_000_000)
            row.update(implementations=["rust", "matlab"], threads=list(THREADS), rhs_count=1, warmups=2, repetitions=7)
            rows.append(row)
    elif kind == "routing":
        for family in FAMILIES:
            row = base(kind, family, 1_000_000)
            row.update(
                implementations=["rust"],
                threads=list(THREADS),
                strategies={"1": ["serial", "auto"], "8": ["serial", "planned", "auto"], "16": ["serial", "planned", "auto"], "32": ["serial", "planned", "auto"]},
                rhs_count=1,
                warmups=2,
                repetitions=5,
            )
            rows.append(row)
    elif kind == "reuse":
        for family in ("worker-firm", "dense-worker-firm"):
            row = base(kind, family, 1_000_000)
            row.update(
                implementations=["rust"], hierarchy_threads=[1, 8, 16, 32], plan_threads=[1, 8, 16, 32],
                solve_threads=[8, 16, 32], variants=["fresh-all", "reuse-hierarchy", "reuse-plan", "reuse-workspace", "serial-no-plan"],
                rhs_count=1, warmups=2, repetitions=7, phase_repetitions=5,
            )
            rows.append(row)
    elif kind == "numa":
        placements = ["numa8-compact", "socket16-compact", "sockets16-split", "linear32", "numa32-spread", "numa32-interleave", "linear32-parallel-touch"]
        for family in ("worker-firm", "dense-worker-firm"):
            row = base(kind, family, 1_000_000)
            row.update(implementations=["rust", "matlab"], placements=placements, rhs_count=1, warmups=2, repetitions=7)
            rows.append(row)
    elif kind == "memory":
        for family, thread_count, implementation, stage, process_repetition in product(
            ("path", "worker-firm", "dense-worker-firm"),
            (1, 16, 32),
            ("rust", "matlab"),
            ("baseline", "input", "graph", "hierarchy", "plan", "workspace-one", "workspace-pool", "solve", "batch"),
            range(1, 6),
        ):
            row = base(kind, family, 1_000_000)
            row.update(
                implementations=[implementation], threads=[thread_count], stages=[stage],
                process_repetition=process_repetition, rhs_count=16, warmups=0, repetitions=1,
            )
            rows.append(row)
    elif kind == "accuracy":
        for family in ("path", "worker-firm", "dense-worker-firm"):
            row = base(kind, family, 1_000_000)
            chosen = int(optimal[family])
            row.update(implementations=["rust", "matlab"], threads=sorted({1, chosen}), tolerances=[1.0e-6, 1.0e-8, 1.0e-10], rhs_count=1, warmups=2, repetitions=5)
            rows.append(row)
    elif kind == "batch":
        for family, vertices, rhs_count, thread_count in product(("worker-firm", "dense-worker-firm"), (300_000, 1_000_000), (1, 4, 16, 64), THREADS):
            row = base(kind, family, vertices)
            row.update(implementations=["rust", "matlab"], threads=[thread_count], rust_strategies=["serial", "planned", "across-rhs", "auto"], matlab_strategies=["native-sequential"], rhs_count=rhs_count, warmups=2, repetitions=5)
            rows.append(row)
    elif kind == "matched-edge":
        values = (("path", 8_000_001), ("grid", 4_000_000), ("worker-firm", 5_333_334), ("dense-worker-firm", 1_000_000))
        for family, vertices in values:
            row = base(kind, family, vertices)
            row.update(implementations=["rust", "matlab"], rust_threads=list(THREADS), matlab_threads=[1, 32], rhs_count=1, warmups=2, repetitions=5)
            rows.append(row)
    elif kind == "fused-smoke":
        for mode in ("homogeneous", "mixed"):
            row = base(kind, "worker-firm", 100_000)
            row.update(
                rhs_count=4,
                mode=mode,
                target_cpu="portable",
                slots=28,
                host_num_proc=28,
                host_cpu_type="E5-2680v4",
                cpu_model_contains="E5-2680 v4",
                warmups=1,
                repetitions=1,
            )
            rows.append(row)
    elif kind == "fused":
        for family, rhs_count, mode in product(
            ("worker-firm", "dense-worker-firm"),
            (4, 16, 32),
            ("homogeneous", "mixed"),
        ):
            row = base(kind, family, 1_000_000)
            row.update(
                rhs_count=rhs_count,
                mode=mode,
                target_cpu="portable",
                slots=28,
                host_num_proc=28,
                host_cpu_type="E5-2680v4",
                cpu_model_contains="E5-2680 v4",
                warmups=2,
                repetitions=7,
            )
            rows.append(row)
    else:
        raise ValueError(f"unknown task kind {kind}")
    for index, row in enumerate(rows, start=1):
        row["task_id"] = index
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("smoke", "baseline", "routing", "reuse", "numa", "memory", "accuracy", "batch", "matched-edge", "fused-smoke", "fused"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--optimal-json", type=Path)
    args = parser.parse_args()
    optimal = {"path": 1, "worker-firm": 16, "dense-worker-firm": 32}
    if args.optimal_json:
        optimal.update({key: int(value) for key, value in json.loads(args.optimal_json.read_text()).items()})
    write_jsonl(args.output, tasks(args.kind, optimal))
    print(f"CMG_SCC2_TASKS_SUCCESS kind={args.kind} tasks={len(tasks(args.kind, optimal))}")


if __name__ == "__main__":
    main()
