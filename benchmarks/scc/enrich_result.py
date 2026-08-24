#!/usr/bin/env python3
"""Attach immutable run, input, host, and /usr/bin/time identity to one result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import socket
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def time_receipt(path: Path) -> dict[str, object]:
    values: dict[str, object] = {}
    if not path.exists():
        return values
    patterns = {
        "user_seconds": r"User time \(seconds\):\s*(\S+)",
        "system_seconds": r"System time \(seconds\):\s*(\S+)",
        "elapsed_wall": r"Elapsed \(wall clock\) time.*:\s*(\S+)",
        "peak_rss_kb": r"Maximum resident set size \(kbytes\):\s*(\d+)",
        "major_page_faults": r"Major \(requiring I/O\) page faults:\s*(\d+)",
        "minor_page_faults": r"Minor \(reclaiming a frame\) page faults:\s*(\d+)",
    }
    content = path.read_text(errors="replace")
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            value = match.group(1)
            values[key] = int(value) if value.isdigit() else value
    return values


def cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo.read_text(), re.MULTILINE)
        if match:
            return match.group(1).strip()
    return platform.processor() or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("time_receipt", type=Path)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("task_id", type=int)
    parser.add_argument("environment_id")
    args = parser.parse_args()

    result = json.loads(args.result.read_text())
    result.update(
        {
            "run_id": args.run_id,
            "task_id": args.task_id,
            "environment_id": args.environment_id,
            "hostname": socket.gethostname(),
            "cpu_model": cpu_model(),
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "sge_job_id": os.environ.get("JOB_ID", "interactive"),
            "sge_task_id": os.environ.get("SGE_TASK_ID", str(args.task_id)),
            "input_hashes": {
                name: sha256(args.input_dir / name)
                for name in ("graph.bin", "rhs.bin", "truth.bin", "metadata.json")
            },
            "process_accounting": time_receipt(args.time_receipt),
        }
    )
    temporary = args.result.with_suffix(args.result.suffix + ".enriched")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.result)


if __name__ == "__main__":
    main()
