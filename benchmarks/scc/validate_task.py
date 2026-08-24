#!/usr/bin/env python3
"""Fail a task unless every expected Rust/MATLAB result is complete and certified."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_root", type=Path)
    parser.add_argument("receipt_root", type=Path)
    parser.add_argument("family")
    parser.add_argument("vertices", type=int)
    parser.add_argument("mode")
    parser.add_argument("thread_set")
    args = parser.parse_args()
    threads = [int(value) for value in args.thread_set.split()]
    seen: set[tuple[str, int]] = set()
    common_hashes = None
    hierarchy: dict[str, tuple[int, ...]] = {}
    for implementation in ("rust", "matlab"):
        for thread_count in threads:
            path = args.task_root / f"{implementation}-t{thread_count}.json"
            require(path.exists(), f"missing {path}")
            result = json.loads(path.read_text())
            identity = (result["implementation"], int(result["threads"]))
            require(identity not in seen, f"duplicate result {identity}")
            seen.add(identity)
            require(result["implementation"] == implementation, f"wrong implementation in {path}")
            require(result["family"] == args.family, f"wrong family in {path}")
            require(int(result["vertices"]) == args.vertices, f"wrong size in {path}")
            require(result["mode"] == args.mode, f"wrong mode in {path}")
            require(bool(result["success"]), f"application marked failure in {path}")
            if implementation == "rust":
                require(float(result["backward_error"]) <= 1.1e-8, f"Rust certification failure in {path}")
            else:
                require(int(result.get("native_pcg_flag", -1)) == 0, f"MATLAB PCG failure in {path}")
                require(float(result["relative_residual"]) <= 1e-7, f"MATLAB residual failure in {path}")
            require(math.isfinite(float(result["backward_error"])), f"nonfinite backward error in {path}")
            require(math.isfinite(float(result["truth_scaled_error"])), f"nonfinite solution diagnostic in {path}")
            for field in (
                "preconditioner_setup_median_ns",
                "preconditioner_apply_median_ns",
                "pcg_median_ns",
                "solver_total_median_ns",
            ):
                require(math.isfinite(float(result[field])) and float(result[field]) >= 0, f"bad {field}")
            hashes = result["input_hashes"]
            if common_hashes is None:
                common_hashes = hashes
            require(hashes == common_hashes, f"input hash mismatch in {path}")
            require(int(result["process_accounting"].get("peak_rss_kb", 0)) > 0, f"missing RSS in {path}")
            current_hierarchy = tuple(int(value) for value in result["level_vertices"])
            prior = hierarchy.setdefault(implementation, current_hierarchy)
            require(prior == current_hierarchy, f"thread-dependent hierarchy in {path}")
    require(len(seen) == 2 * len(threads), "incomplete result set")
    (args.receipt_root / "validation.json").write_text(
        json.dumps({"success": True, "results": len(seen), "input_hashes": common_hashes}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
