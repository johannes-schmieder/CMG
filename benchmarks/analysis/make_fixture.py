#!/usr/bin/env python3
"""Create a complete synthetic result tree for plot/report pipeline tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FAMILIES = {
    "path": 1.0,
    "grid": 1.6,
    "worker-firm": 1.3,
    "dense-worker-firm": 4.5,
    "weak-community": 1.8,
}
SIZES = [100_000, 300_000, 1_000_000]
THREADS = [1, 2, 4, 8, 16, 32]


def result(family: str, factor: float, vertices: int, mode: str, implementation: str, threads: int) -> dict:
    edges = int(vertices * ({"path": 1, "grid": 2, "worker-firm": 1.5, "dense-worker-firm": 8, "weak-community": 2}[family]))
    language = 0.72 if implementation == "rust" else 1.0
    scaling = 1 + 0.55 * (threads - 1) ** 0.58
    setup = factor * language * vertices * 250 / scaling
    solve = factor * language * vertices * 430 / scaling
    batch_count = 16 if mode == "batch16" else 1
    solve *= batch_count * (0.72 if batch_count == 16 else 1)
    total = setup + solve
    iterations = int(8 + factor * 2)
    return {
        "schema": 1,
        "run_id": "fixture-run",
        "environment_id": "fixture-environment",
        "implementation": implementation,
        "source_commit": "fixture",
        "upstream_commit": "19752fc102f8cae8e34f66457bfaccb1aaa60375" if implementation == "matlab" else "",
        "family": family,
        "mode": mode,
        "vertices": vertices,
        "canonical_edges": edges,
        "matrix_nonzeros": vertices + 2 * edges,
        "threads": threads,
        "repetitions": 3,
        "batch_count": batch_count,
        "input_load_ns": vertices * 20,
        "graph_build_ns": vertices * 40,
        "preconditioner_setup_samples_ns": [setup * 0.98, setup, setup * 1.03],
        "preconditioner_setup_median_ns": setup,
        "parallel_plan_setup_samples_ns": [vertices * 2] * 3 if implementation == "rust" else [0] * 3,
        "parallel_plan_setup_median_ns": vertices * 2 if implementation == "rust" else 0,
        "preconditioner_apply_loops": 16,
        "preconditioner_apply_samples_ns": [solve / iterations] * 3,
        "preconditioner_apply_median_ns": solve / iterations,
        "pcg_samples_ns": [solve * 0.99, solve, solve * 1.02],
        "pcg_median_ns": solve,
        "solver_total_samples_ns": [total * 0.99, total, total * 1.02],
        "solver_total_median_ns": total,
        "iterations": iterations,
        "native_pcg_flag": 0,
        "residual_norm": 1e-9,
        "relative_residual": 1e-9,
        "backward_error": 1e-10,
        "truth_scaled_error": 1e-8,
        "levels": 4,
        "level_vertices": [vertices, vertices // 3, vertices // 10, 500],
        "level_matrix_nonzeros": [vertices + 2 * edges, vertices, vertices // 2, 10_000],
        "repeat_counts": [1, 1, 2, 0],
        "hostname": "scc-gd4",
        "cpu_model": "Intel(R) Xeon(R) Gold 6242 CPU @ 2.80GHz",
        "input_hashes": {"graph.bin": "fixture", "rhs.bin": "fixture", "truth.bin": "fixture", "metadata.json": "fixture"},
        "process_accounting": {"peak_rss_kb": int(vertices * factor / 18)},
        "success": True,
    }


def kernel(family: str, vertices: int) -> dict:
    edges = vertices - 1 if family == "path" else vertices * 3 // 2
    loops = max(16, 40_000_000 // edges)
    projection_loops = max(16, 80_000_000 // vertices)
    return {
        "schema": 3,
        "case": family,
        "vertices": vertices,
        "canonical_edges": edges,
        "loops": loops,
        "rust_median_ns": vertices * loops * 4,
        "c_median_ns": vertices * loops * 3,
        "rust_over_c": 4 / 3,
        "projection": {
            "loops": projection_loops,
            "restriction_rust_median_ns": vertices * projection_loops * 2,
            "restriction_c_median_ns": vertices * projection_loops * 1.7,
            "prolongation_rust_median_ns": vertices * projection_loops * 1.4,
            "prolongation_c_median_ns": vertices * projection_loops * 1.2,
        },
        "cycle": {
            "dimension": min(vertices, 20_000),
            "loops": 16,
            "rust_median_ns": min(vertices, 20_000) * 16 * 8,
            "c_median_ns": min(vertices, 20_000) * 16 * 7,
            "rust_over_c": 8 / 7,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    task = 0
    for family, factor in FAMILIES.items():
        for vertices in SIZES:
            task += 1
            root = args.output / "output" / f"task-{task}"
            root.mkdir(parents=True, exist_ok=True)
            for implementation in ("rust", "matlab"):
                for threads in THREADS:
                    value = result(family, factor, vertices, "single", implementation, threads)
                    (root / f"{implementation}-t{threads}.json").write_text(json.dumps(value) + "\n")
            if family in ("path", "worker-firm"):
                (root / "c-kernel.json").write_text(json.dumps(kernel(family, vertices)) + "\n")
    for family in ("worker-firm", "dense-worker-firm"):
        for vertices in (300_000, 1_000_000):
            task += 1
            root = args.output / "output" / f"task-{task}"
            root.mkdir(parents=True, exist_ok=True)
            for implementation in ("rust", "matlab"):
                for threads in THREADS:
                    value = result(family, FAMILIES[family], vertices, "batch16", implementation, threads)
                    (root / f"{implementation}-t{threads}.json").write_text(json.dumps(value) + "\n")
    print(f"CMG_FIXTURE_SUCCESS tasks={task}")


if __name__ == "__main__":
    main()
