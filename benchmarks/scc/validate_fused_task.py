#!/usr/bin/env python3
"""Validate one SCC fused-RHS experiment result and its provenance."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    run_root, task_file, task_id_text = sys.argv[1:]
    run_root = Path(run_root)
    task_id = int(task_id_text)
    tasks = [json.loads(line) for line in Path(task_file).read_text().splitlines() if line]
    task = tasks[task_id - 1]
    output_root = run_root / "output" / task["experiment"] / f"task-{task_id}"
    receipt_root = run_root / "receipts" / task["experiment"] / f"task-{task_id}"
    result = json.loads((output_root / "fused.json").read_text())
    require(result["protocol_version"] == "cmg-fused-rhs-v1", "wrong fused protocol")
    require(result["run_id"] == run_root.name and result["task_id"] == task_id, "wrong task identity")
    require(result["source_commit"] == (run_root / "manifests/source-commit.txt").read_text().strip(), "wrong source")
    require(result["source_archive_sha256"] == (run_root / "manifests/source-archive-sha256.txt").read_text().strip(), "wrong archive")
    for key in ("family", "vertices", "rhs_count", "mode", "target_cpu", "warmups", "repetitions"):
        require(result[key] == task[key], f"wrong {key}")
    require(result["bitwise_identical"] is True, "fused output is not bitwise identical")
    require(len(result["scalar_ns"]) == task["repetitions"], "wrong scalar sample count")
    require(len(result["fused_ns"]) == task["repetitions"], "wrong fused sample count")
    require(all(int(value) > 0 for value in result["scalar_ns"] + result["fused_ns"]), "invalid timing")
    ratio = float(result["fused_over_scalar"])
    interval = [float(value) for value in result["paired_bootstrap_ratio_ci95"]]
    require(math.isfinite(ratio) and all(math.isfinite(value) for value in interval), "nonfinite ratio")
    require(interval[0] <= ratio <= interval[1], "ratio outside confidence interval")
    require(int(result["fused_workspace_bytes"]) > 0, "missing fused workspace memory")
    expected_binary = (run_root / "manifests" / f"fused-{task['target_cpu']}-binary-sha256.txt").read_text().strip()
    require(result["binary_sha256"] == expected_binary, "wrong fused binary hash")
    require(len(result["allocated_cpus"]) == 32, "unexpected CPU allocation")
    require((receipt_root / "SUCCESS").is_file(), "missing success receipt")
    print(f"CMG_FUSED_VALIDATE_SUCCESS task={task_id} ratio={ratio:.6f}")


if __name__ == "__main__":
    main()
