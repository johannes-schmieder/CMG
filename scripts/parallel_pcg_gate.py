"""Qualify and conditionally retain the opt-in ParallelCmgPlan PCG path."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any

ROOT = Path.cwd()
RESULT_PATH = ROOT / ".ci/performance/parallel-pcg-latest.json"
PLAN_PATH = ROOT / "PERFORMANCE_PLAN.md"
WORKFLOW_PATH = ROOT / ".github/workflows/parallel-pcg-gate.yml"
GATE_PATH = ROOT / "scripts/parallel_pcg_gate.py"
CANDIDATE_PATH = ROOT / "scripts/add_parallel_pcg_candidate.py"
TOUCHED_EXISTING = [
    "src/preconditioner.rs",
    "src/pcg.rs",
    "src/lib.rs",
    "tests/parallel.rs",
    "benchmarks/Cargo.lock",
]
NEW_FILES = ["benchmarks/src/bin/parallel-pcg-solve.rs"]
command_output: list[str] = []


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    command_output.append(f"$ {' '.join(command)}\n{completed.stdout}")
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def restore_candidate() -> None:
    run(["git", "checkout", "HEAD", "--", *TOUCHED_EXISTING], timeout=120)
    for name in NEW_FILES:
        Path(name).unlink(missing_ok=True)


def sample(binary: Path, arguments: list[Any], tag: str) -> dict[str, Any]:
    time_path = Path(f"/tmp/cmg-parallel-pcg-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            str(time_path),
            str(binary),
            *[str(value) for value in arguments],
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output for {tag}: {payloads}")
    timing = time_path.read_text()
    rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", timing)
    if rss is None:
        raise RuntimeError(f"peak RSS missing for {tag}")
    payloads[0]["peak_rss_kib"] = int(rss.group(1))
    return payloads[0]


def stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    timing = {
        "serial_median_ns",
        "parallel_median_ns",
        "speedup",
        "plan_build_ns",
        "peak_rss_kib",
    }
    return {key: value for key, value in payload.items() if key not in timing}


def update_plan(result: dict[str, Any]) -> None:
    if not PLAN_PATH.exists():
        return
    status = "retained" if result.get("accepted") else "not retained"
    geometric = result.get("geometric_speedup")
    minimum = result.get("minimum_speedup")
    evidence = ""
    if geometric is not None and minimum is not None:
        evidence = (
            f" Full-solve four-thread geometric speedup: `{geometric:.3f}x`; "
            f"minimum case speedup: `{minimum:.3f}x`."
        )
    checkpoint = f'''### Parallel PCG checkpoint — 2026-08-23

- The opt-in prebuilt-plan PCG candidate was **{status}**.{evidence}
- Validation status: `{result.get("validation", "unknown")}`.
- Existing serial PCG remained unchanged throughout the experiment.
- Machine-readable evidence:
  `.ci/performance/parallel-pcg-latest.json`.

'''
    text = PLAN_PATH.read_text()
    marker = "## Current next action\n"
    if marker not in text:
        raise RuntimeError("PERFORMANCE_PLAN current-next-action heading missing")
    if "### Parallel PCG checkpoint — 2026-08-23" not in text:
        text = text.replace(marker, checkpoint + marker, 1)
    PLAN_PATH.write_text(text)


def cleanup_staging() -> None:
    WORKFLOW_PATH.unlink(missing_ok=True)
    GATE_PATH.unlink(missing_ok=True)
    CANDIDATE_PATH.unlink(missing_ok=True)


baseline_sha = run(["git", "rev-parse", "HEAD"]).stdout.strip()
result: dict[str, Any] = {
    "schema_version": 1,
    "experiment": "parallel-pcg-prebuilt-plan",
    "baseline_sha": baseline_sha,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "cases": {},
}

try:
    run(["python", str(CANDIDATE_PATH)])
    run(["cargo", "fmt", "--all"])
    run([
        "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"
    ])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run([
        "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"
    ])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run([
        "cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"
    ])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--document-private-items", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    run([
        "cargo", "build", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--release"
    ])
    result["validation"] = "success"

    binary = ROOT / "benchmarks/target/release/parallel-pcg-solve"
    specs = [
        ("path-250k", ["path", 250_000, 3, 4]),
        ("worker-firm-300k", ["worker-firm", 100_000, 3, 4]),
        ("worker-firm-600k", ["worker-firm", 200_000, 3, 4]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 3, 4]),
    ]
    speedups: list[float] = []
    maximum_difference = 0.0
    maximum_plan_bytes_per_edge = 0.0
    all_iteration_counts_match = True
    for name, arguments in specs:
        observations = [sample(binary, arguments, f"{name}-{index}") for index in range(2)]
        if stable_payload(observations[0]) != stable_payload(observations[1]):
            raise RuntimeError(f"non-timing benchmark metadata changed for {name}")
        serial_ns = statistics.median(item["serial_median_ns"] for item in observations)
        parallel_ns = statistics.median(item["parallel_median_ns"] for item in observations)
        plan_build_ns = statistics.median(item["plan_build_ns"] for item in observations)
        case = stable_payload(observations[0])
        speedup = serial_ns / parallel_ns
        saved_ns = serial_ns - parallel_ns
        break_even = plan_build_ns / saved_ns if saved_ns > 0 else None
        case.update(
            {
                "serial_median_ns": serial_ns,
                "parallel_median_ns": parallel_ns,
                "speedup": speedup,
                "plan_build_ns": plan_build_ns,
                "break_even_rhs": break_even,
                "peak_rss_kib": max(item["peak_rss_kib"] for item in observations),
                "plan_bytes_per_edge": (
                    case["plan_bytes"] / case["edges"] if case["edges"] else 0.0
                ),
            }
        )
        result["cases"][name] = case
        speedups.append(speedup)
        maximum_difference = max(maximum_difference, case["max_scaled_difference"])
        maximum_plan_bytes_per_edge = max(
            maximum_plan_bytes_per_edge, case["plan_bytes_per_edge"]
        )
        all_iteration_counts_match &= (
            case["serial_iterations"] == case["parallel_iterations"]
        )

    geometric_speedup = math.exp(
        sum(math.log(value) for value in speedups) / len(speedups)
    )
    path = result["cases"]["path-250k"]
    small = result["cases"]["worker-firm-300k"]
    large = result["cases"]["worker-firm-600k"]
    dense = result["cases"]["dense-worker-firm-800k"]
    result.update(
        {
            "geometric_speedup": geometric_speedup,
            "minimum_speedup": min(speedups),
            "maximum_scaled_difference": maximum_difference,
            "maximum_plan_bytes_per_edge": maximum_plan_bytes_per_edge,
            "all_iteration_counts_match": all_iteration_counts_match,
            "acceptance_limits": {
                "maximum_scaled_difference": 5.0e-9,
                "all_iteration_counts_match": True,
                "geometric_speedup_min": 1.10,
                "path_operator_count": 0,
                "path_speedup_min": 0.90,
                "small_worker_firm_speedup_min": 0.95,
                "large_worker_firm_speedup_min": 1.04,
                "dense_worker_firm_speedup_min": 1.30,
                "plan_bytes_per_edge_max": 128.0,
            },
        }
    )
    result["accepted"] = (
        maximum_difference <= 5.0e-9
        and all_iteration_counts_match
        and geometric_speedup >= 1.10
        and path["operators"] == 0
        and path["speedup"] >= 0.90
        and small["speedup"] >= 0.95
        and large["speedup"] >= 1.04
        and dense["speedup"] >= 1.30
        and maximum_plan_bytes_per_edge <= 128.0
    )
    result["decision_reason"] = (
        "full qualification passed; prebuilt routed CMG plan accelerated certified PCG solves without changing iterations"
        if result["accepted"]
        else "qualification passed but numerical, iteration, memory, or end-to-end speed gates were not met"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    result["last_command_output"] = "\n\n".join(command_output)[-30000:]
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    restore_candidate()

RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
RESULT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_plan(result)
cleanup_staging()

run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: add opt-in prebuilt-plan parallel PCG"
    if result["accepted"]
    else "perf: record parallel PCG experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
