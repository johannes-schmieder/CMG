#!/usr/bin/env python3
"""Validate the exact shape and host contract of one fused-RHS manifest."""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

PROFILES_PATH = Path(__file__).resolve().parent / "fused_cpu_profiles.json"
CPU_PROFILES = json.loads(PROFILES_PATH.read_text())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def expected_shape(experiment: str) -> set[tuple[str, int, str, int]]:
    if experiment == "fused-smoke":
        return {("worker-firm", 4, mode, 100_000) for mode in ("homogeneous", "mixed")}
    if experiment == "fused":
        return {
            (family, rhs_count, mode, 1_000_000)
            for family, rhs_count, mode in product(
                ("worker-firm", "dense-worker-firm"),
                (4, 16, 32),
                ("homogeneous", "mixed"),
            )
        }
    if experiment.startswith("fused-cpu-smoke-"):
        return {("worker-firm", 4, "homogeneous", 100_000)}
    if experiment.startswith("fused-cpu-screen-"):
        return {
            (family, 16, mode, 1_000_000)
            for family, mode in product(
                ("worker-firm", "dense-worker-firm"),
                ("homogeneous", "mixed"),
            )
        }
    raise SystemExit(f"unknown fused experiment: {experiment}")


def expected_profile(experiment: str) -> dict[str, object]:
    if experiment in ("fused-smoke", "fused"):
        return CPU_PROFILES["e5-2680v4"]
    for prefix in ("fused-cpu-smoke-", "fused-cpu-screen-"):
        if experiment.startswith(prefix):
            profile = experiment.removeprefix(prefix)
            require(profile in CPU_PROFILES, f"unknown fused CPU profile: {profile}")
            return CPU_PROFILES[profile]
    raise SystemExit(f"unknown fused experiment: {experiment}")


def validate(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    require(bool(rows), "empty fused manifest")
    experiment = rows[0]["experiment"]
    require(path.stem == experiment, "manifest filename does not match experiment")
    profile = expected_profile(experiment)
    observed_shape: set[tuple[str, int, str, int]] = set()
    for number, value in enumerate(rows, 1):
        require(value["task_id"] == number, f"wrong task id at row {number}")
        require(value["experiment"] == experiment, f"mixed experiment at row {number}")
        require(value["target_cpu"] == "portable", f"nonportable task at row {number}")
        for key, expected in profile.items():
            require(value[key] == expected, f"wrong {key} at row {number}")
        require(value["warmups"] >= 1, f"missing warmup at row {number}")
        require(value["repetitions"] >= 1, f"missing repetition at row {number}")
        observed_shape.add(
            (value["family"], value["rhs_count"], value["mode"], value["vertices"])
        )
    require(len(observed_shape) == len(rows), "duplicate fused task")
    require(observed_shape == expected_shape(experiment), "wrong fused task matrix")


def main() -> None:
    manifest = Path(sys.argv[1])
    validate(manifest)
    print(f"CMG_FUSED_MANIFEST_VALIDATE_SUCCESS experiment={manifest.stem}")


if __name__ == "__main__":
    main()
