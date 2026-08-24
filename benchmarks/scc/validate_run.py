#!/usr/bin/env python3
"""Validate scheduler accounting and application receipts for a complete run."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path


def task_lines(path: Path) -> list[tuple[str, int, str, int]]:
    parsed = []
    for line in path.read_text().splitlines():
        family, vertices, mode, rhs_count = line.split("\t")
        parsed.append((family, int(vertices), mode, int(rhs_count)))
    return parsed


def accounting_value(content: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("task_file", type=Path)
    parser.add_argument("job_id")
    parser.add_argument("thread_set")
    args = parser.parse_args()
    threads = [int(value) for value in args.thread_set.replace(":", " ").split()]
    tasks = task_lines(args.task_file)
    expected_source = (args.run_root / "manifests" / "source-commit.txt").read_text().strip()
    expected_environment = (
        args.run_root / "manifests" / "environment-id.txt"
    ).read_text().strip()
    summary: dict[str, object] = {
        "success": True,
        "job_id": args.job_id,
        "tasks": len(tasks),
        "threads": threads,
        "accounting": [],
        "warnings": [],
        "hierarchy_differences": [],
    }
    identities = set()
    for task_id, (family, vertices, mode, _) in enumerate(tasks, start=1):
        success = args.run_root / "receipts" / f"task-{task_id}" / "SUCCESS"
        if not success.exists():
            raise SystemExit(f"missing application receipt {success}")
        validation = args.run_root / "receipts" / f"task-{task_id}" / "validation.json"
        validation_result = json.loads(validation.read_text())
        if not validation_result.get("success"):
            raise SystemExit(f"invalid application receipt {validation}")
        hierarchy: dict[str, tuple[int, ...]] = {}
        for implementation in ("rust", "matlab"):
            for thread_count in threads:
                path = args.run_root / "output" / f"task-{task_id}" / f"{implementation}-t{thread_count}.json"
                result = json.loads(path.read_text())
                identity = (family, vertices, mode, implementation, thread_count)
                if identity in identities:
                    raise SystemExit(f"duplicate result {identity}")
                identities.add(identity)
                if not result.get("success"):
                    raise SystemExit(f"failed application result {path}")
                if result.get("run_id") != args.run_root.name:
                    raise SystemExit(f"wrong run identity in {path}")
                if result.get("source_commit") != expected_source:
                    raise SystemExit(f"wrong source identity in {path}")
                if result.get("environment_id") != expected_environment:
                    raise SystemExit(f"wrong environment identity in {path}")
                if (
                    result.get("family") != family
                    or int(result.get("vertices", -1)) != vertices
                    or result.get("mode") != mode
                    or result.get("implementation") != implementation
                    or int(result.get("threads", -1)) != thread_count
                ):
                    raise SystemExit(f"wrong result identity in {path}")
                if result.get("input_hashes") != validation_result.get("input_hashes"):
                    raise SystemExit(f"input hash mismatch in {path}")
                if int(result.get("process_accounting", {}).get("peak_rss_kb", 0)) <= 0:
                    raise SystemExit(f"missing process RSS in {path}")
                for median_key, samples_key in (
                    ("preconditioner_setup_median_ns", "preconditioner_setup_samples_ns"),
                    ("preconditioner_apply_median_ns", "preconditioner_apply_samples_ns"),
                    ("pcg_median_ns", "pcg_samples_ns"),
                    ("solver_total_median_ns", "solver_total_samples_ns"),
                ):
                    samples = [float(value) for value in result.get(samples_key, [])]
                    if len(samples) != int(result.get("repetitions", -1)):
                        raise SystemExit(f"wrong repetition count in {path}: {samples_key}")
                    if not all(math.isfinite(value) and value >= 0 for value in samples):
                        raise SystemExit(f"invalid repetition in {path}: {samples_key}")
                    if float(result.get(median_key, math.nan)) != statistics.median(samples):
                        raise SystemExit(f"median mismatch in {path}: {median_key}")
                if implementation == "rust":
                    if float(result.get("backward_error", math.inf)) > 1.1e-8:
                        raise SystemExit(f"Rust certification failure in {path}")
                elif (
                    int(result.get("native_pcg_flag", -1)) != 0
                    or float(result.get("relative_residual", math.inf)) > 1.0e-7
                ):
                    raise SystemExit(f"MATLAB PCG failure in {path}")
                current_hierarchy = tuple(int(value) for value in result["level_vertices"])
                prior_hierarchy = hierarchy.setdefault(implementation, current_hierarchy)
                if current_hierarchy != prior_hierarchy:
                    raise SystemExit(f"thread-dependent hierarchy in {path}")
                for warning in result.get("warnings", []):
                    summary["warnings"].append(
                        {
                            "task_id": task_id,
                            "implementation": implementation,
                            "threads": thread_count,
                            "warning": warning,
                        }
                    )
        if hierarchy["rust"] != hierarchy["matlab"]:
            summary["hierarchy_differences"].append(
                {
                    "task_id": task_id,
                    "rust": list(hierarchy["rust"]),
                    "matlab": list(hierarchy["matlab"]),
                }
            )
        if family in ("path", "worker-firm") and mode == "single":
            kernel_path = args.run_root / "output" / f"task-{task_id}" / "c-kernel.json"
            kernel = json.loads(kernel_path.read_text())
            if kernel.get("source_commit") != expected_source:
                raise SystemExit(f"wrong source identity in {kernel_path}")
            if kernel.get("upstream_commit") != "19752fc102f8cae8e34f66457bfaccb1aaa60375":
                raise SystemExit(f"wrong upstream identity in {kernel_path}")
            if kernel.get("case") != family or int(kernel.get("vertices", -1)) != vertices:
                raise SystemExit(f"wrong kernel identity in {kernel_path}")
        accounting_path = args.run_root / "receipts" / "accounting" / f"{args.job_id}.{task_id}.txt"
        content = accounting_path.read_text()
        failed = accounting_value(content, "failed")
        exit_status = accounting_value(content, "exit_status")
        if failed != "0" or exit_status != "0":
            raise SystemExit(
                f"scheduler failure for task {task_id}: failed={failed} exit_status={exit_status}"
            )
        summary["accounting"].append(
            {
                "task_id": task_id,
                "hostname": accounting_value(content, "hostname"),
                "wallclock": accounting_value(content, "ru_wallclock"),
                "maxvmem": accounting_value(content, "maxvmem"),
                "failed": 0,
                "exit_status": 0,
            }
        )
    expected = len(tasks) * len(threads) * 2
    if len(identities) != expected:
        raise SystemExit(f"expected {expected} identities, found {len(identities)}")
    output = args.run_root / "receipts" / "RUN_VALIDATION.json"
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"CMG_RUN_VALIDATION_SUCCESS tasks={len(tasks)} results={len(identities)}")


if __name__ == "__main__":
    main()
