#!/usr/bin/env python3
"""Run one immutable SCC fused-RHS experiment task."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def task_roots(run_root: Path, experiment: str, task_id: int) -> tuple[Path, Path]:
    """Return kind-scoped roots so smoke and full task IDs cannot collide."""
    return (
        run_root / "output" / experiment / f"task-{task_id}",
        run_root / "receipts" / experiment / f"task-{task_id}",
    )


def is_fused_experiment(experiment: str) -> bool:
    """Return whether an experiment name is a safe fused-RHS namespace."""
    if experiment in ("fused", "fused-smoke"):
        return True
    return bool(re.fullmatch(r"fused-cpu-(?:smoke|screen)-[a-z0-9]+(?:-[a-z0-9]+)*", experiment))


def cpu_model_name() -> str:
    """Return the Linux processor model used for benchmark provenance."""
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("missing CPU model name")


def main() -> None:
    run_id, task_file, task_id_text = sys.argv[1:]
    task_id = int(task_id_text)
    project_root = Path("/projectnb/welfgr/cmg-benchmarks")
    run_root = project_root / "runs" / run_id
    source = (run_root / "manifests/source-commit.txt").read_text().strip()
    archive = (run_root / "manifests/source-archive-sha256.txt").read_text().strip()
    code_root = project_root / "code-b2" / source
    tasks = [json.loads(line) for line in Path(task_file).read_text().splitlines() if line]
    task = tasks[task_id - 1]
    if task["task_id"] != task_id or not is_fused_experiment(task["experiment"]):
        raise SystemExit("invalid fused task identity")
    target_cpu = task["target_cpu"]
    if target_cpu == "portable":
        target = "target"
    elif target_cpu == "cascadelake":
        target = "target-cascadelake"
    else:
        raise SystemExit("invalid fused target CPU")
    binary = code_root / "benchmarks" / target / "release/fused-rhs-experiment"
    output_root, receipt_root = task_roots(run_root, task["experiment"], task_id)
    output_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary),
        task["family"],
        str(task["vertices"]),
        str(task["rhs_count"]),
        task["mode"],
        str(task["warmups"]),
        str(task["repetitions"]),
    ]
    started = time.time_ns()
    process = subprocess.run(command, check=True, text=True, capture_output=True)
    elapsed = time.time_ns() - started
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    result = json.loads(lines[-1])
    allocated_cpus = sorted(os.sched_getaffinity(0))
    result.update(
        protocol_version="cmg-fused-rhs-v1",
        run_id=run_id,
        task_id=task_id,
        experiment=task["experiment"],
        target_cpu=target_cpu,
        source_commit=source,
        source_archive_sha256=archive,
        binary_sha256=sha256(binary),
        allocated_cpus=allocated_cpus,
        allocated_slots=len(allocated_cpus),
        host_num_proc=os.cpu_count(),
        hostname=socket.gethostname(),
        cpu_model=cpu_model_name(),
        process_wall_ns=elapsed,
    )
    destination = output_root / "fused.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(destination)
    (receipt_root / "SUCCESS").write_text(
        f"success=true\nbinary_sha256={result['binary_sha256']}\n"
    )
    print(f"CMG_FUSED_TASK_SUCCESS task={task_id} target={target_cpu}")


if __name__ == "__main__":
    main()
