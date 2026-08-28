#!/usr/bin/env python3
"""Discover physical-core, socket, and NUMA placements inside an SGE cpuset."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path


def run(args: list[str]) -> str:
    completed = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise SystemExit(f"command failed: {' '.join(args)}\n{completed.stderr}")
    return completed.stdout


def expand_cpu_list(value: str) -> list[int]:
    result: list[int] = []
    for part in value.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def compact(values: list[int]) -> str:
    return ",".join(str(value) for value in values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    allowed_line = next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("Cpus_allowed_list:"))
    allowed = set(expand_cpu_list(allowed_line.split(":", 1)[1]))
    rows = []
    raw_table = run(["lscpu", "-e=CPU,NODE,SOCKET,CORE,ONLINE,MAXMHZ,MINMHZ", "-J"])
    parsed = json.loads(raw_table)
    seen_cores: set[tuple[int, int]] = set()
    for item in parsed["cpus"]:
        cpu = int(item["cpu"])
        if cpu not in allowed or str(item.get("online", "yes")).lower() not in ("yes", "y", "1", "true"):
            continue
        socket = int(item["socket"])
        core = int(item["core"])
        key = (socket, core)
        if key in seen_cores:
            continue
        seen_cores.add(key)
        rows.append({"cpu": cpu, "node": int(item["node"]), "socket": socket, "core": core, "maxmhz": item.get("maxmhz"), "minmhz": item.get("minmhz")})
    rows.sort(key=lambda item: (item["socket"], item["node"], item["core"], item["cpu"]))
    by_node: dict[int, list[int]] = defaultdict(list)
    by_socket: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        by_node[row["node"]].append(row["cpu"])
        by_socket[row["socket"]].append(row["cpu"])
    physical = [row["cpu"] for row in rows]
    nodes = sorted(by_node)
    sockets = sorted(by_socket)
    if len(physical) < 32:
        raise SystemExit(f"whole-node benchmark needs 32 physical CPUs; discovered {len(physical)}")
    placements = {
        "thread1": physical[:1],
        "thread8": physical[:8],
        "thread16": physical[:16],
        "thread32": physical[:32],
        "numa8-compact": by_node[nodes[0]][:8],
        "socket16-compact": by_socket[sockets[0]][:16],
        "sockets16-split": by_socket[sockets[0]][:8] + by_socket[sockets[1]][:8],
        "linear32": physical[:32],
        "numa32-spread": [cpu for index in range(max(len(by_node[node]) for node in nodes)) for node in nodes for cpu in by_node[node][index:index+1]][:32],
        "numa32-interleave": physical[:32],
        "linear32-parallel-touch": physical[:32],
    }
    payload = {
        "schema": 1,
        "hostname": os.uname().nodename,
        "allowed_cpus": sorted(allowed),
        "physical_cores": rows,
        "nodes": {str(key): value for key, value in sorted(by_node.items())},
        "sockets": {str(key): value for key, value in sorted(by_socket.items())},
        "placements": {key: {"cpus": value, "cpu_list": compact(value), "nodes": sorted({next(row["node"] for row in rows if row["cpu"] == cpu) for cpu in value}), "sockets": sorted({next(row["socket"] for row in rows if row["cpu"] == cpu) for cpu in value})} for key, value in placements.items()},
        "lscpu_json": json.loads(run(["lscpu", "-J"])),
        "numactl_hardware": subprocess.run(["numactl", "--hardware"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False).stdout,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(f"CMG_SCC2_TOPOLOGY_SUCCESS physical={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
