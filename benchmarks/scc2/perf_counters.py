#!/usr/bin/env python3
"""Run one separate perf event-group pass and preserve explicit support state."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path


GROUPS = {
    "core": ("task-clock", "cycles", "instructions", "ref-cycles"),
    "scheduler": ("context-switches", "cpu-migrations", "page-faults"),
    "cache-branch": ("cache-references", "cache-misses", "branches", "branch-misses"),
}


def support_probe(perf: str, event: str) -> tuple[bool, str]:
    completed = subprocess.run(
        [perf, "stat", "-e", event, "true"],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    return completed.returncode == 0, completed.stderr[-4000:]


def numeric(value: str) -> float | None:
    value = value.strip().replace(" ", "")
    if value.startswith("<"):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_group", choices=tuple(GROUPS))
    parser.add_argument("output", type=Path)
    parser.add_argument("--stage", default="whole-process-diagnostic")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("missing command after --")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    perf = shutil.which("perf")
    records = []
    supported = []
    for event in GROUPS[args.event_group]:
        if perf is None:
            ok, probe = False, "perf executable unavailable"
        else:
            ok, probe = support_probe(perf, event)
        if ok:
            supported.append(event)
        else:
            records.append(
                {
                    "event_group": args.event_group,
                    "event": event,
                    "stage": args.stage,
                    "support_status": "unsupported" if perf else "unsupported",
                    "value": None,
                    "unit": None,
                    "time_enabled": None,
                    "time_running": None,
                    "running_percentage": None,
                    "command": " ".join(command),
                    "probe_error": probe,
                }
            )
    raw_path = args.output.with_suffix(".perf.csv")
    log_path = args.output.with_suffix(".command.log")
    returncode = 0
    if supported and perf:
        perf_command = [
            perf, "stat", "-x,", "--no-big-num", "-o", str(raw_path),
            "-e", ",".join(supported), "--", *command,
        ]
        with log_path.open("wb") as handle:
            completed = subprocess.run(perf_command, stdout=handle, stderr=subprocess.STDOUT, check=False)
        returncode = completed.returncode
        parsed_events = set()
        for line in raw_path.read_text(errors="replace").splitlines() if raw_path.exists() else []:
            if not line or line.startswith("#"):
                continue
            fields = line.split(",")
            if len(fields) < 5:
                continue
            event = fields[2].removesuffix(":u")
            if event not in supported:
                continue
            parsed_events.add(event)
            value = numeric(fields[0])
            enabled = numeric(fields[3])
            percentage = numeric(fields[4])
            running = None if enabled is None or percentage is None else enabled * percentage / 100.0
            status = "failed" if returncode else ("multiplexed" if percentage is not None and percentage < 90.0 else "supported")
            records.append(
                {
                    "event_group": args.event_group,
                    "event": event,
                    "stage": args.stage,
                    "support_status": status,
                    "value": value,
                    "unit": fields[1] or None,
                    "time_enabled": enabled,
                    "time_running": running,
                    "running_percentage": percentage,
                    "command": " ".join(command),
                    "perf_command": " ".join(perf_command),
                    "application_returncode": returncode,
                }
            )
        for event in supported:
            if event not in parsed_events:
                records.append(
                    {
                        "event_group": args.event_group,
                        "event": event,
                        "stage": args.stage,
                        "support_status": "failed",
                        "value": None,
                        "time_enabled": None,
                        "time_running": None,
                        "command": " ".join(command),
                        "application_returncode": returncode,
                        "probe_error": "perf produced no parseable event row",
                    }
                )
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps({"records": records}, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    if returncode:
        raise SystemExit(returncode)
    print(f"CMG_SCC2_COUNTER_SUCCESS group={args.event_group} records={len(records)}")


if __name__ == "__main__":
    main()
