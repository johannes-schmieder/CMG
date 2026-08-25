#!/usr/bin/env python3
"""Record SCC diagnostic-tool and kernel-counter capabilities without guessing."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


TOOLS = ("perf", "numactl", "numastat", "pidstat", "pcm-memory", "likwid-perfctr", "lstopo-no-graphics", "hwloc-ls")


def command(args: list[str]) -> dict:
    completed = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return {"command": args, "returncode": completed.returncode, "output": completed.stdout[-20000:]}


def read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    tools = {}
    for name in TOOLS:
        resolved = shutil.which(name)
        tools[name] = {"available": resolved is not None, "path": resolved}
        if resolved:
            tools[name]["version_probe"] = command([resolved, "--version"])
    perf_probe = {"support_status": "unsupported", "probe": None}
    if tools["perf"]["available"]:
        probe = command([tools["perf"]["path"], "stat", "-x,", "-e", "task-clock,cycles,instructions", "true"])
        perf_probe = {
            "support_status": "supported" if probe["returncode"] == 0 else ("permission-denied" if "permission" in probe["output"].lower() else "failed"),
            "probe": probe,
        }
    payload = {
        "schema": 1,
        "hostname": os.uname().nodename,
        "kernel": command(["uname", "-a"]),
        "tools": tools,
        "perf_event_paranoid": read("/proc/sys/kernel/perf_event_paranoid"),
        "numa_balancing": read("/proc/sys/kernel/numa_balancing"),
        "transparent_hugepage": read("/sys/kernel/mm/transparent_hugepage/enabled"),
        "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "microcode": read("/sys/devices/system/cpu/cpu0/microcode/version"),
        "cpus_allowed_list": next((line.split(":", 1)[1].strip() for line in (read("/proc/self/status") or "").splitlines() if line.startswith("Cpus_allowed_list:")), None),
        "perf_probe": perf_probe,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(f"CMG_SCC2_CAPABILITIES_SUCCESS output={args.output}")


if __name__ == "__main__":
    main()
