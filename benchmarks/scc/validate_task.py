#!/usr/bin/env python3
"""Strictly validate every configuration emitted by one SCC2 task."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix import expand  # noqa: E402
from protocol import PROTOCOL_VERSION, read_jsonl, sha256_file, validate_run_id  # noqa: E402


PRIMARY_STAGES = ("pcg_solve", "solver_total")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def all_rhs(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = result["numerical"]["all_rhs"]
    return value if isinstance(value, list) else [value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("task_file", type=Path)
    parser.add_argument("task_id", type=int)
    args = parser.parse_args()
    validate_run_id(args.run_root.name)
    tasks = read_jsonl(args.task_file)
    require(1 <= args.task_id <= len(tasks), "task id outside manifest")
    task = tasks[args.task_id - 1]
    require(task["task_id"] == args.task_id, "task line identity mismatch")
    expected = {config["configuration_id"]: config for config in expand(task)}
    output_root = args.run_root / "output" / f"task-{args.task_id}"
    receipt_root = args.run_root / "receipts" / f"task-{args.task_id}"
    schema = json.loads(
        (Path(__file__).parent / "schemas/result.schema.json").read_text()
    )
    memory_schema = json.loads(
        (Path(__file__).parent / "schemas/memory.schema.json").read_text()
    )
    counter_schema = json.loads(
        (Path(__file__).parent / "schemas/counter.schema.json").read_text()
    )
    task_schema = json.loads(
        (Path(__file__).parent / "schemas/task.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(task_schema).validate(task)
    source_commit = (args.run_root / "manifests/source-commit.txt").read_text().strip()
    archive_sha = (args.run_root / "manifests/source-archive-sha256.txt").read_text().strip()
    environment_id = (args.run_root / "manifests/environment-id.txt").read_text().strip()
    rust_identity = json.loads((args.run_root / "manifests/rust-identity.json").read_text())
    mex_sha = (args.run_root / "manifests/mex-binary-sha256.txt").read_text().strip()
    memory_binary_sha = (
        args.run_root / "manifests/memory-binary-sha256.txt"
    ).read_text().strip()
    require(rust_identity["source_commit"] == source_commit, "compiled Rust commit mismatch")
    require(rust_identity["source_archive_sha256"] == archive_sha, "compiled archive digest mismatch")
    require("unknown" not in rust_identity.values(), "compiled Rust identity contains unknown")
    topology = json.loads((receipt_root / "topology.json").read_text())
    allowed_cpus = {int(value) for value in topology["allowed_cpus"]}

    found: dict[str, dict[str, Any]] = {}
    common_hashes = None
    for path in sorted(output_root.glob("*.json")):
        result = json.loads(path.read_text())
        selected_schema = memory_schema if task["experiment"] == "memory" else schema
        jsonschema.Draft202012Validator(selected_schema).validate(result)
        identifier = result.get("configuration_id")
        require(identifier in expected, f"unexpected configuration {identifier} in {path}")
        require(identifier not in found, f"duplicate configuration {identifier}")
        config = expected[identifier]
        found[identifier] = result
        if task["experiment"] == "memory":
            require(result["success"] is True, f"memory-stage failure in {path}")
            require(result["run_id"] == args.run_root.name, f"wrong run id in {path}")
            require(result["task_id"] == args.task_id, f"wrong task id in {path}")
            require(result["source_commit"] == source_commit, f"wrong commit in {path}")
            require(result["source_archive_sha256"] == archive_sha, f"wrong archive digest in {path}")
            require(result["environment_id"] == environment_id, f"wrong environment in {path}")
            require(result["implementation"] == config["implementation"], f"wrong implementation in {path}")
            require(result["family"] == task["family"], f"wrong family in {path}")
            require(result["stage"] == config["stage_stop"], f"wrong memory stage in {path}")
            require(int(result["threads"]) == int(config["solve_threads"]), f"wrong threads in {path}")
            require(int(result["process_repetition"]) == int(task["process_repetition"]), f"wrong process repetition in {path}")
            expected_memory_binary = memory_binary_sha if result["implementation"] == "rust" else mex_sha
            require(result["binary_sha256"] == expected_memory_binary, f"wrong memory binary in {path}")
            require(int(result["peak_rss_kb"]) > 0, f"missing process peak RSS in {path}")
            require(result["checkpoints"][-1]["stage"] == result["stage"], f"wrong final checkpoint in {path}")
            cpus = [value for value in result["placement"]["cpu_list"].split(",") if value]
            require(len(cpus) == int(result["threads"]), f"memory binding width mismatch in {path}")
            require({int(value) for value in cpus}.issubset(allowed_cpus), f"memory binding outside allocation in {path}")
            hashes = result["input_hashes"]
            if common_hashes is None:
                common_hashes = hashes
            require(hashes == common_hashes, f"fixture hash mismatch in {path}")
            if result["stage"] in ("solve", "batch"):
                require(result["numerical"] is not None, f"missing memory numerical check in {path}")
                require(float(result["numerical"]["max_backward_error"]) <= 1.1e-8, f"memory numerical failure in {path}")
            continue
        require(result["success"] is True, f"application failure in {path}")
        require(result["protocol_version"] == PROTOCOL_VERSION, f"wrong protocol in {path}")
        require(result["run_id"] == args.run_root.name, f"wrong run id in {path}")
        require(result["task_id"] == args.task_id, f"wrong task id in {path}")
        require(result["source_commit"] == source_commit, f"wrong commit in {path}")
        require(result["source_archive_sha256"] == archive_sha, f"wrong archive digest in {path}")
        require(result["environment_id"] == environment_id, f"wrong environment in {path}")
        require(result["family"] == task["family"], f"wrong family in {path}")
        require(int(result["vertices"]) == int(task["vertices"]), f"wrong vertices in {path}")
        require(result["implementation"] == config["implementation"], f"wrong implementation in {path}")
        for key in ("hierarchy_threads", "plan_threads", "solve_threads", "rhs_count"):
            require(int(result[key]) == int(config[key]), f"wrong {key} in {path}")
        require(result["strategy"] == config["strategy"], f"wrong strategy in {path}")
        require(result["variant"] == config["variant"], f"wrong variant in {path}")
        require(float(result["tolerance"]) == float(config["tolerance"]), f"wrong tolerance in {path}")
        expected_binary = rust_identity["binary_sha256"] if result["implementation"] == "rust" else mex_sha
        require(result["binary_sha256"] == expected_binary, f"binary identity mismatch in {path}")
        require(int(result["canonical_edges"]) > 0, f"missing edges in {path}")
        require(int(result["matrix_nonzeros"]) > int(result["vertices"]), f"invalid matrix nonzeros in {path}")
        hashes = result["input_hashes"]
        if common_hashes is None:
            common_hashes = hashes
        require(hashes == common_hashes, f"fixture hash mismatch in {path}")
        require(all(len(value) == 64 for value in hashes.values()), f"invalid fixture digest in {path}")

        placement = result["placement"]
        cpus = [value for value in placement["cpu_list"].split(",") if value]
        require(len(cpus) == int(result["solve_threads"]), f"binding width mismatch in {path}")
        require(len(cpus) == len(set(cpus)), f"duplicate CPU in binding for {path}")
        require({int(value) for value in cpus}.issubset(allowed_cpus), f"binding outside allocation in {path}")
        require(int(result["memory"]["peak_rss_kb"]) > 0, f"missing peak RSS in {path}")
        require(int(result["process_accounting"]["peak_rss_kb"]) > 0, f"missing time receipt RSS in {path}")

        measured = [sample for sample in result["samples"] if sample.get("measured", True)]
        counts = Counter(sample["stage"] for sample in measured)
        for stage in PRIMARY_STAGES:
            require(counts[stage] == int(task["repetitions"]), f"wrong sample count for {stage} in {path}")
        setup_stage = (
            "preconditioner_setup"
            if config["variant"] == "fresh-all"
            else "preconditioner_setup_reused"
        )
        require(counts[setup_stage] == int(task["repetitions"]), f"wrong setup semantics in {path}")
        if config["variant"] == "serial-no-plan":
            plan_stage = "parallel_plan_setup_omitted"
        elif config["variant"] in ("reuse-plan", "reuse-workspace"):
            plan_stage = "parallel_plan_setup_reused"
        elif result["implementation"] == "matlab":
            plan_stage = None
        else:
            plan_stage = "parallel_plan_setup"
        if plan_stage:
            require(counts[plan_stage] == int(task["repetitions"]), f"wrong plan semantics in {path}")
        workspace_stage = (
            "workspace_allocation_reused"
            if config["variant"] == "reuse-workspace"
            else "workspace_allocation"
        )
        require(counts[workspace_stage] == int(task["repetitions"]), f"wrong workspace semantics in {path}")
        if int(task["rhs_count"]) == 1:
            require(counts["preconditioner_apply"] == int(task["repetitions"]), f"wrong apply samples in {path}")
        for sample in measured:
            wall = float(sample["wall_ns"])
            cpu = sample["process_cpu_ns"]
            require(math.isfinite(wall) and wall >= 0, f"invalid wall time in {path}")
            require(cpu is not None and math.isfinite(float(cpu)) and float(cpu) >= 0, f"missing CPU time in {path}")
        for stage in (*PRIMARY_STAGES, setup_stage, workspace_stage):
            values = [int(sample["wall_ns"]) for sample in measured if sample["stage"] == stage]
            require(statistics.median(values) >= 0, f"invalid median for {stage} in {path}")

        diagnostics = all_rhs(result)
        require(len(diagnostics) == int(result["rhs_count"]), f"wrong numerical RHS count in {path}")
        require(result["numerical"]["converged"] is True, f"native convergence failure in {path}")
        for diagnostic in diagnostics:
            for key in (
                "independent_relative_residual",
                "backward_error",
                "reference_scaled_error",
                "energy_norm_error",
            ):
                require(math.isfinite(float(diagnostic[key])), f"nonfinite {key} in {path}")
            require(
                float(diagnostic["backward_error"]) <= max(1.1 * float(result["tolerance"]), 1.1e-10),
                f"backward-error certification failure in {path}",
            )
            if result["implementation"] == "matlab":
                require(int(diagnostic["native_flag"]) == 0, f"MATLAB PCG flag in {path}")

    require(set(found) == set(expected), f"configuration grid mismatch: missing={set(expected)-set(found)}")
    counter_records = 0
    if task["experiment"] in ("smoke", "baseline"):
        for identifier in expected:
            for event_group in ("core", "scheduler", "cache-branch"):
                path = receipt_root / "counters" / f"{event_group}-{identifier}.json"
                require(path.exists(), f"missing counter group {path}")
                payload = json.loads(path.read_text())
                require(payload.get("records"), f"empty counter group {path}")
                for record in payload["records"]:
                    jsonschema.Draft202012Validator(counter_schema).validate(record)
                    require(record["configuration_id"] == identifier, f"counter identity mismatch in {path}")
                    if record["support_status"] == "supported":
                        require(record["value"] is not None, f"missing supported counter in {path}")
                        require(float(record["running_percentage"]) >= 90.0, f"multiplexed counter mislabeled in {path}")
                    elif record["support_status"] in ("unsupported", "permission-denied"):
                        require(record["value"] is None, f"unsupported counter has a value in {path}")
                    counter_records += 1
    if task["experiment"] == "routing" and task["family"] in (
        "path", "worker-firm", "dense-worker-firm"
    ):
        auxiliary = receipt_root / "auxiliary-profiles"
        expected_auxiliary = ["hierarchy-phases.jsonl", "contraction-subphases.jsonl"] + [
            f"plan-phases-t{threads}.json" for threads in (1, 8, 16, 32)
        ]
        for name in expected_auxiliary:
            path = auxiliary / name
            require(path.exists() and path.stat().st_size > 0, f"missing auxiliary profile {path}")
            parsed = [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]
            require(parsed, f"no JSON records in auxiliary profile {path}")
    receipt = json.loads((receipt_root / "APPLICATION_SUCCESS.json").read_text())
    require(set(receipt["configurations"]) == set(expected), "application receipt grid mismatch")
    validation = {
        "success": True,
        "run_id": args.run_root.name,
        "task_id": args.task_id,
        "configurations": len(found),
        "input_hashes": common_hashes,
        "source_commit": source_commit,
        "source_archive_sha256": archive_sha,
        "counter_records": counter_records,
    }
    temporary = receipt_root / "VALIDATION.tmp"
    temporary.write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n")
    temporary.replace(receipt_root / "VALIDATION.json")
    print(f"CMG_SCC2_TASK_VALIDATION_SUCCESS task={args.task_id} configurations={len(found)}")


if __name__ == "__main__":
    main()
