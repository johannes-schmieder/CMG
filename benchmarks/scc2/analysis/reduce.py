#!/usr/bin/env python3
"""Deterministically reduce accepted SCC2 JSON into inspectable report tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import PROTOCOL_VERSION, canonical_json, sha256_file  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def timing_stats(values: list[float], seed_text: str) -> dict[str, float]:
    require(bool(values) and all(math.isfinite(value) and value >= 0 for value in values), f"invalid timing group {seed_text}")
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    generator = random.Random(seed)
    bootstrap = [
        statistics.median(generator.choices(values, k=len(values)))
        for _ in range(10_000)
    ]
    return {
        "median_s": median,
        "iqr_s": percentile(values, 0.75) - percentile(values, 0.25),
        "mad_s": statistics.median(deviations),
        "min_s": min(values),
        "max_s": max(values),
        "bootstrap_low_s": percentile(bootstrap, 0.025),
        "bootstrap_high_s": percentile(bootstrap, 0.975),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_results(run: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in sorted((run / "output").glob("task-*/*.json")):
        yield path, json.loads(path.read_text())


def common(result: dict[str, Any]) -> dict[str, Any]:
    placement = result.get("placement", {})
    return {
        "protocol_version": result["protocol_version"],
        "run_id": result["run_id"],
        "task_id": result["task_id"],
        "configuration_id": result["configuration_id"],
        "source_commit": result["source_commit"],
        "source_archive_sha256": result["source_archive_sha256"],
        "binary_sha256": result["binary_sha256"],
        "upstream_commit": result.get("upstream_commit", ""),
        "environment_id": result["environment_id"],
        "host": result.get("host", ""),
        "implementation": result["implementation"],
        "experiment": result.get("experiment", "memory"),
        "family": result["family"],
        "vertices": result["vertices"],
        "canonical_edges": result["canonical_edges"],
        "matrix_nonzeros": result["matrix_nonzeros"],
        "rhs_count": result["rhs_count"],
        "strategy": result.get("strategy", "memory-stage"),
        "actual_strategy": result.get("actual_strategy", result.get("strategy", "memory-stage")),
        "route_reason": result.get("route_reason", ""),
        "variant": result.get("variant", "fresh-all"),
        "hierarchy_threads": result.get("hierarchy_threads", result.get("threads", 0)),
        "plan_threads": result.get("plan_threads", 0),
        "solve_threads": result.get("solve_threads", result.get("threads", 0)),
        "cpu_list": placement.get("cpu_list", ""),
        "socket_list": placement.get("socket_list", ""),
        "numa_node_list": placement.get("numa_node_list", ""),
        "placement_mode": placement.get("mode", ""),
        "memory_policy": placement.get("memory_policy", ""),
        "first_touch_policy": placement.get("first_touch_policy", ""),
        "tolerance": result.get("tolerance", 1.0e-8),
        "max_iterations": result.get("max_iterations", 1_000),
        "warmups": result.get("warmups", 0),
        "repetitions": result.get("repetitions", 1),
        "matlab_release": result.get("matlab_release", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--latest-json", type=Path, required=True)
    args = parser.parse_args()
    for run in args.run:
        validation = run / "receipts/RUN_VALIDATION.json"
        require(validation.exists(), f"unvalidated run: {run}")
        require(json.loads(validation.read_text()).get("success") is True, f"failed validation: {run}")

    samples: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    hierarchy: list[dict[str, Any]] = []
    memory: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    counters: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    result_count = 0

    for run in args.run:
        for path, result in read_results(run):
            result_count += 1
            require(result["protocol_version"] == PROTOCOL_VERSION, f"wrong protocol in {path}")
            base = common(result)
            if result.get("record_type") == "memory-stage":
                accounting = result["process_accounting"]
                memory.append(
                    {
                        **base,
                        "process_repetition": result["process_repetition"],
                        "stage": result["stage"],
                        "peak_rss_kb": result["peak_rss_kb"],
                        "peak_rss_kb_driver": result["peak_rss_kb_driver"],
                        "user_seconds": accounting["user_seconds"],
                        "system_seconds": accounting["system_seconds"],
                        **{f"owned_{key}_bytes": value for key, value in result["owned_bytes"].items()},
                    }
                )
                for warning in result.get("warnings", []):
                    warnings.append({**base, "warning": warning, "stage": result["stage"]})
                continue

            measured = [sample for sample in result["samples"] if sample.get("measured", True)]
            stage_values: dict[str, list[float]] = defaultdict(list)
            configuration_samples: list[dict[str, Any]] = []
            for sample in measured:
                wall_s = float(sample["wall_ns"]) / 1.0e9
                cpu_ns = sample["process_cpu_ns"]
                cpu_s = None if cpu_ns is None else float(cpu_ns) / 1.0e9
                active = None if cpu_s is None or wall_s == 0 else cpu_s / wall_s
                sample_row = {
                        **base,
                        "repetition": sample["repetition"],
                        "order_position": sample["order_position"],
                        "execution_sequence": result["execution_sequence"],
                        "started_at_utc": sample["started_at_utc"],
                        "stage": sample["stage"],
                        "wall_s": wall_s,
                        "process_cpu_s": cpu_s,
                        "average_active_cpus": active,
                        "cpu_slot_seconds": wall_s * float(base["solve_threads"]),
                    }
                samples.append(sample_row)
                configuration_samples.append(sample_row)
                stage_values[sample["stage"]].append(wall_s)
            summary = {
                **base,
                "input_load_s": float(result["input_load_ns"]) / 1.0e9,
                "graph_assembly_s": float(result["graph_assembly_ns"]) / 1.0e9,
                "hierarchy_levels": result["hierarchy"]["levels"],
                "plan_operator_count": result["hierarchy"]["plan_operator_count"],
                "graph_bytes": result["memory"]["graph_bytes"],
                "hierarchy_bytes": result["memory"]["hierarchy_bytes"],
                "terminal_factor_bytes": result["memory"]["terminal_factor_bytes"],
                "plan_bytes": result["memory"]["plan_bytes"],
                "workspace_bytes_each": result["memory"]["workspace_bytes_each"],
                "workspace_pool_bytes": result["memory"]["workspace_pool_bytes"],
                "workspace_concurrency": result.get("workspace_concurrency", 1),
                "peak_rss_kb": result["memory"]["peak_rss_kb"],
            }
            for stage, values in sorted(stage_values.items()):
                for statistic, value in timing_stats(values, f"{base['run_id']}:{base['configuration_id']}:{stage}").items():
                    summary[f"{stage}_{statistic}"] = value
            numerical = result["numerical"]["all_rhs"]
            numerical = numerical if isinstance(numerical, list) else [numerical]
            for field in (
                "iterations", "restarts", "native_relative_residual",
                "independent_relative_residual", "backward_error",
                "reference_scaled_error", "energy_norm_error",
            ):
                values = [float(row[field]) for row in numerical]
                summary[f"{field}_median"] = statistics.median(values)
                summary[f"{field}_max"] = max(values)
            summary_rows.append(summary)
            for phase in result.get("phases", []):
                phases.append(
                    {
                        **base,
                        "repetition": phase["repetition"],
                        "level": phase.get("level"),
                        "phase": phase["phase"],
                        "wall_s": float(phase["wall_ns"]) / 1.0e9,
                        "process_cpu_s": None if phase.get("process_cpu_ns") is None else float(phase["process_cpu_ns"]) / 1.0e9,
                        "calls": phase["calls"],
                    }
                )
            plan_by_level = {int(item["level"]): item for item in result.get("plan_levels", [])}
            for level, (vertices, nonzeros, repeat) in enumerate(
                zip(result["hierarchy"]["vertices"], result["hierarchy"]["matrix_nonzeros"], result["hierarchy"]["repeats"])
            ):
                plan_level = plan_by_level.get(level, {})
                hierarchy.append(
                    {
                        **base,
                        "level": level,
                        "level_vertices": vertices,
                        "level_matrix_nonzeros": nonzeros,
                        "repeat": repeat,
                        "terminal_reason": result["hierarchy"]["terminal_reason"],
                        "plan_operator_count_total": result["hierarchy"]["plan_operator_count"],
                        "plan_operator_eligible": plan_level.get("eligible"),
                        "plan_eligibility_reason": plan_level.get("reason"),
                        "plan_retained_bytes": plan_level.get("retained_bytes", 0),
                    }
                )
            for warning in result.get("warnings", []):
                warnings.append({**base, "warning": warning, "hierarchy_flag": result["hierarchy"].get("flag")})
            if int(result["rhs_count"]) > 1:
                pcg = [sample for sample in configuration_samples if sample["stage"] == "pcg_solve"]
                for sample in pcg:
                    batch.append(
                        {
                            **base,
                            "repetition": sample["repetition"],
                            "batch_wall_s": sample["wall_s"],
                            "seconds_per_rhs": sample["wall_s"] / int(result["rhs_count"]),
                            "rhs_per_second": int(result["rhs_count"]) / sample["wall_s"],
                            "workspace_concurrency": result.get("workspace_concurrency", 1),
                            "workspace_pool_bytes": result["memory"]["workspace_pool_bytes"],
                            "peak_rss_kb": result["memory"]["peak_rss_kb"],
                        }
                    )
        for path in sorted((run / "receipts").glob("task-*/counters/*.json")):
            value = json.loads(path.read_text())
            records = value if isinstance(value, list) else value.get("records", [value])
            counters.extend(records)

    data = args.report_root / "data"
    counter_values: dict[tuple[str, int, str], dict[str, float]] = defaultdict(dict)
    for record in counters:
        if record.get("support_status") == "supported" and record.get("value") is not None:
            key = (record["run_id"], int(record["task_id"]), record["configuration_id"])
            counter_values[key][record["event"]] = float(record["value"])
    for row in summary_rows:
        values = counter_values.get((row["run_id"], int(row["task_id"]), row["configuration_id"]), {})
        cycles = values.get("cycles")
        instructions = values.get("instructions")
        task_clock_ms = values.get("task-clock")
        cache_references = values.get("cache-references")
        cache_misses = values.get("cache-misses")
        branches = values.get("branches")
        branch_misses = values.get("branch-misses")
        row["counter_ipc"] = None if not cycles or instructions is None else instructions / cycles
        row["counter_effective_ghz"] = None if not cycles or not task_clock_ms else cycles / (task_clock_ms * 1.0e6)
        row["counter_cache_miss_rate"] = None if not cache_references or cache_misses is None else cache_misses / cache_references
        row["counter_branch_miss_rate"] = None if not branches or branch_misses is None else branch_misses / branches
        row["counter_context_switches"] = values.get("context-switches")
        row["counter_cpu_migrations"] = values.get("cpu-migrations")
        row["counter_page_faults"] = values.get("page-faults")
    write_csv(data / "results.csv", summary_rows)
    write_csv(data / "samples.csv", samples)
    write_csv(data / "phases.csv", phases)
    write_csv(data / "counters.csv", counters)
    write_csv(data / "memory.csv", memory)
    write_csv(data / "hierarchy.csv", hierarchy)
    write_csv(data / "batch.csv", batch)
    write_csv(data / "warnings.csv", warnings)
    latest = {
        "protocol_version": PROTOCOL_VERSION,
        "run_ids": [run.name for run in args.run],
        "result_configurations": len(summary_rows),
        "memory_processes": len(memory),
        "raw_stage_samples": len(samples),
        "phase_records": len(phases),
        "counter_records": len(counters),
        "warnings": len(warnings),
        "data_sha256": {
            path.name: sha256_file(path)
            for path in sorted(data.glob("*.csv"))
        },
    }
    args.latest_json.parent.mkdir(parents=True, exist_ok=True)
    args.latest_json.write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
    print(f"CMG_SCC2_REDUCE_SUCCESS configurations={len(summary_rows)} samples={len(samples)} phases={len(phases)} memory={len(memory)}")


if __name__ == "__main__":
    main()
