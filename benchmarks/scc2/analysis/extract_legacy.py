#!/usr/bin/env python3
"""Recover raw first-study samples and process receipts without changing them."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import sha256_file  # noqa: E402


STAGES = {
    "preconditioner_setup": "preconditioner_setup_samples_ns",
    "parallel_plan_setup": "parallel_plan_setup_samples_ns",
    "preconditioner_apply": "preconditioner_apply_samples_ns",
    "pcg_solve": "pcg_samples_ns",
    "solver_total": "solver_total_samples_ns",
}


def time_fields(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(errors="replace").splitlines():
        if ": " in line:
            key, value = line.strip().split(": ", 1)
            result[key] = value
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-run", type=Path, required=True)
    parser.add_argument("--batch-run", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    args = parser.parse_args()
    sample_rows: list[dict[str, Any]] = []
    hierarchy_rows: list[dict[str, Any]] = []
    batch_rows: list[dict[str, Any]] = []
    warning_rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"runs": [], "unavailable": []}

    for run_root in (args.main_run, args.batch_run):
        run_entry: dict[str, Any] = {
            "run_id": run_root.name,
            "tree": str(run_root),
            "files": 0,
            "results": 0,
            "hosts": {},
            "manifest_hashes": {},
        }
        for manifest in sorted((run_root / "manifests").glob("*")):
            if manifest.is_file():
                run_entry["manifest_hashes"][manifest.name] = sha256_file(manifest)
        for path in sorted((run_root / "output").glob("task-*/*.json")):
            result = json.loads(path.read_text())
            if result.get("implementation") not in ("rust", "matlab"):
                continue
            run_entry["results"] += 1
            task_id = int(result["task_id"])
            implementation = result["implementation"]
            threads = int(result["threads"])
            stem = f"{implementation}-t{threads}"
            receipt = run_root / "receipts" / f"task-{task_id}" / f"{stem}.time"
            raw_time = time_fields(receipt)
            accounting = result.get("process_accounting", {})
            common = {
                "run_id": run_root.name,
                "task_id": task_id,
                "family": result["family"],
                "vertices": result["vertices"],
                "canonical_edges": result["canonical_edges"],
                "implementation": implementation,
                "threads": threads,
                "mode": result["mode"],
                "source_commit": result["source_commit"],
                "environment_id": result["environment_id"],
                "hostname": result["hostname"],
                "executed_at_utc": result["executed_at_utc"],
                "user_seconds": accounting.get("user_seconds"),
                "system_seconds": accounting.get("system_seconds"),
                "elapsed_process_wall": accounting.get("elapsed_wall"),
                "peak_rss_kb": accounting.get("peak_rss_kb"),
                "major_faults": raw_time.get("Major (requiring I/O) page faults", accounting.get("major_page_faults")),
                "minor_faults": raw_time.get("Minor (reclaiming a frame) page faults", accounting.get("minor_page_faults")),
                "voluntary_context_switches": raw_time.get("Voluntary context switches"),
                "involuntary_context_switches": raw_time.get("Involuntary context switches"),
                "input_hashes": json.dumps(result["input_hashes"], sort_keys=True),
            }
            run_entry["hosts"].setdefault(str(task_id), result["hostname"])
            for stage, key in STAGES.items():
                values = result.get(key, [])
                for repetition, wall_ns in enumerate(values, start=1):
                    sample_rows.append({**common, "stage": stage, "repetition": repetition, "wall_ns": wall_ns})
            for level, (vertices, nonzeros, repeat) in enumerate(
                zip(result["level_vertices"], result["level_matrix_nonzeros"], result["repeat_counts"])
            ):
                hierarchy_rows.append(
                    {
                        **{key: common[key] for key in ("run_id", "task_id", "family", "vertices", "implementation", "threads")},
                        "level": level,
                        "level_vertices": vertices,
                        "level_matrix_nonzeros": nonzeros,
                        "repeat": repeat,
                        "plan_operators_total": result.get("plan_operators", 0),
                        "plan_bytes_total": result.get("plan_bytes", 0),
                        "workspace_bytes": result.get("workspace_bytes", 0),
                        "hierarchy_flag": result.get("hierarchy_flag"),
                    }
                )
            for warning in result.get("warnings", []):
                warning_rows.append({**common, "warning": warning, "hierarchy_flag": result.get("hierarchy_flag")})
            if result["mode"] == "batch16":
                for rhs_index, diagnostics in enumerate(
                    zip(
                        result["batch_iterations"], result["batch_relative_residuals"],
                        result["batch_backward_errors"], result["batch_truth_scaled_errors"],
                    ),
                    start=1,
                ):
                    batch_rows.append(
                        {
                            **{key: common[key] for key in ("run_id", "task_id", "family", "vertices", "implementation", "threads")},
                            "rhs_index": rhs_index,
                            "iterations": diagnostics[0],
                            "independent_relative_residual": diagnostics[1],
                            "backward_error": diagnostics[2],
                            "reference_scaled_error": diagnostics[3],
                            "batch_time_median_ns": result["pcg_median_ns"],
                            "seconds_per_rhs": float(result["pcg_median_ns"]) / 16.0e9,
                        }
                    )
        run_entry["files"] = sum(1 for path in run_root.rglob("*") if path.is_file())
        validation = run_root / "receipts/RUN_VALIDATION.json"
        if validation.exists():
            run_entry["run_validation_sha256"] = sha256_file(validation)
            run_entry["run_validation"] = json.loads(validation.read_text())
        evidence["runs"].append(run_entry)
    evidence["unavailable"] = [
        "The first driver recorded one configuration timestamp, not per-repetition timestamps.",
        "The first driver did not record per-stage process CPU time.",
        "The first time-receipt parser did not retain peak PSS or per-stage RSS.",
    ]
    data = args.report_root / "data"
    write_csv(data / "legacy-main-samples.csv", sample_rows)
    write_csv(data / "legacy-main-hierarchy.csv", hierarchy_rows)
    write_csv(data / "legacy-batch16.csv", batch_rows)
    write_csv(data / "legacy-warnings.csv", warning_rows)
    (data / "legacy-main-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"CMG_SCC2_LEGACY_SUCCESS samples={len(sample_rows)} hierarchy={len(hierarchy_rows)} batch={len(batch_rows)}")


if __name__ == "__main__":
    main()
