#!/usr/bin/env python3
"""Validate a complete SCC2 array, including qacct evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix import expand  # noqa: E402
from protocol import read_jsonl, validate_run_id  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def field(content: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("task_file", type=Path)
    parser.add_argument("job_id")
    args = parser.parse_args()
    validate_run_id(args.run_root.name)
    tasks = read_jsonl(args.task_file)
    job_id = args.job_id.split(".", 1)[0]
    accounting = []
    total_configurations = 0
    for task in tasks:
        task_id = int(task["task_id"])
        validation_path = args.run_root / "receipts" / f"task-{task_id}" / "VALIDATION.json"
        require(validation_path.exists(), f"missing validation receipt for task {task_id}")
        validation = json.loads(validation_path.read_text())
        require(validation.get("success") is True, f"invalid task receipt {validation_path}")
        expected = len(expand(task))
        require(validation.get("configurations") == expected, f"wrong task grid in {validation_path}")
        total_configurations += expected
        path = args.run_root / "receipts/accounting" / f"{job_id}.{task_id}.txt"
        require(path.exists(), f"missing qacct for task {task_id}")
        content = path.read_text()
        failed = field(content, "failed")
        exit_status = field(content, "exit_status")
        require(failed == "0" and exit_status == "0", f"scheduler failure task {task_id}: failed={failed} exit_status={exit_status}")
        accounting.append(
            {
                "task_id": task_id,
                "hostname": field(content, "hostname"),
                "failed": int(failed),
                "exit_status": int(exit_status),
                "wallclock": field(content, "ru_wallclock"),
                "maxvmem": field(content, "maxvmem"),
                "slots": field(content, "slots"),
            }
        )
    summary = {
        "success": True,
        "run_id": args.run_root.name,
        "job_id": job_id,
        "tasks": len(tasks),
        "configurations": total_configurations,
        "accounting": accounting,
    }
    output = args.run_root / "receipts/RUN_VALIDATION.json"
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"CMG_SCC2_RUN_VALIDATION_SUCCESS tasks={len(tasks)} configurations={total_configurations}")


if __name__ == "__main__":
    main()
