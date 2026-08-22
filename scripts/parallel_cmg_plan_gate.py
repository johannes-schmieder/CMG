from __future__ import annotations

import base64
import gzip
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
from typing import Any

ROOT = Path.cwd()
PATCH_RECORD = ROOT / "scripts/parallel_cmg_plan.patch.gz.b64"
RESULT_PATH = ROOT / ".ci/performance/parallel-cmg-plan-latest.json"
PLAN_PATH = ROOT / "PERFORMANCE_PLAN.md"
STATUS_PATH = ROOT / "PERFORMANCE_STATUS.md"
WORKFLOW_PATH = ROOT / ".github/workflows/parallel-cmg-plan.yml"
SCRIPT_PATH = ROOT / "scripts/parallel_cmg_plan_gate.py"

TOUCHED_EXISTING = [
    "benchmarks/Cargo.toml",
    "src/coarsen.rs",
    "src/csr.rs",
    "src/graph.rs",
    "src/lib.rs",
    "src/preconditioner.rs",
    "tests/parallel.rs",
]
TOUCHED_NEW = ["benchmarks/src/bin/parallel-cmg-apply.rs"]


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 5400,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
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
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def restore_candidate() -> None:
    run(["git", "checkout", "HEAD", "--", *TOUCHED_EXISTING], check=False)
    for relative in TOUCHED_NEW:
        (ROOT / relative).unlink(missing_ok=True)
    run(["cargo", "fmt", "--all"], check=False)
    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
        ],
        check=False,
    )


def invariant_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "threads",
            "operators",
            "loops",
            "max_scaled_difference",
            "plan_bytes",
            "workspace_bytes",
        )
    }


def sample(binary: Path, arguments: list[object], tag: str) -> dict[str, Any]:
    time_path = Path(f"/tmp/cmg-parallel-plan-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            str(time_path),
            str(binary),
            *[str(value) for value in arguments],
        ],
        timeout=5400,
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    timing = time_path.read_text()
    peak_line = next(
        (
            line
            for line in timing.splitlines()
            if "Maximum resident set size (kbytes):" in line
        ),
        None,
    )
    if peak_line is None:
        raise RuntimeError("peak RSS missing")
    peak_rss_kib = int(peak_line.rsplit(":", 1)[1].strip())
    payload = payloads[0]
    payload["peak_rss_kib"] = peak_rss_kib
    return payload


def update_documents(result: dict[str, Any]) -> None:
    status = "retained" if result.get("accepted") else "not retained"
    speedup = result.get("geometric_speedup")
    worst = result.get("minimum_speedup")
    evidence = ""
    if speedup is not None and worst is not None:
        evidence = (
            f" Four-thread geometric speedup: `{speedup:.3f}x`; "
            f"minimum case speedup: `{worst:.3f}x`."
        )
    checkpoint = f"""### Parallel CMG application checkpoint — 2026-08-22

- The optional `ParallelCmgPlan` candidate was **{status}**.{evidence}
- Full serial/all-feature formatting, Clippy, rustdoc, debug/release tests,
  release builds, and benchmark-crate qualification: `{result.get('validation')}`.
- Machine-readable evidence:
  `.ci/performance/parallel-cmg-plan-latest.json`.

"""
    if PLAN_PATH.exists():
        text = PLAN_PATH.read_text()
        marker = "## Current next action\n"
        if marker in text and "### Parallel CMG application checkpoint — 2026-08-22" not in text:
            text = text.replace(marker, checkpoint + marker, 1)
        PLAN_PATH.write_text(text)

    if STATUS_PATH.exists():
        text = STATUS_PATH.read_text()
        active = "## Active benchmark gate\n"
        replacement = f"""## Latest resolved benchmark gate

- Optional `ParallelCmgPlan`: **{status}**.{evidence}
- Evidence: `.ci/performance/parallel-cmg-plan-latest.json`.

## Active benchmark gate
"""
        if "## Latest resolved benchmark gate" not in text and active in text:
            text = text.replace(active, replacement, 1)
        STATUS_PATH.write_text(text)


def cleanup_staging() -> None:
    WORKFLOW_PATH.unlink(missing_ok=True)
    SCRIPT_PATH.unlink(missing_ok=True)
    PATCH_RECORD.unlink(missing_ok=True)
    scripts = ROOT / "scripts"
    try:
        scripts.rmdir()
    except OSError:
        pass


baseline_sha = run(["git", "rev-parse", "HEAD"]).stdout.strip()
result: dict[str, Any] = {
    "schema_version": 1,
    "experiment": "parallel-cmg-plan",
    "baseline_sha": baseline_sha,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "cases": {},
}

try:
    if not PATCH_RECORD.exists():
        raise RuntimeError("parallel CMG patch payload is missing")
    if (ROOT / ".github/workflows/direct-compact-contraction.yml").exists():
        raise RuntimeError("direct compact-contraction gate is still active")

    patch_bytes = gzip.decompress(base64.b64decode(PATCH_RECORD.read_bytes()))
    patch_path = Path("/tmp/parallel-cmg-plan.patch")
    patch_path.write_bytes(patch_bytes)
    run(["git", "apply", "--check", str(patch_path)])
    run(["git", "apply", str(patch_path)])

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all",
            "--",
            "--check",
        ]
    )
    run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ]
    )
    run(
        [
            "cargo",
            "clippy",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ]
    )
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(
        [
            "cargo",
            "doc",
            "--no-deps",
            "--document-private-items",
            "--all-features",
        ],
        env=doc_env,
    )
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--all-features", "--release"])
    run(
        [
            "cargo",
            "build",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--all-targets",
            "--release",
        ]
    )
    result["validation"] = "success"

    binary = ROOT / "benchmarks/target/release/parallel-cmg-apply"
    specs = [
        ("path-250k", ["path", 250_000, 5, 4]),
        ("worker-firm-300k", ["worker-firm", 100_000, 5, 4]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 5, 4]),
    ]
    speedups: list[float] = []
    for name, arguments in specs:
        first = sample(binary, arguments, f"{name}-first")
        second = sample(binary, arguments, f"{name}-second")
        if invariant_payload(first) != invariant_payload(second):
            raise RuntimeError(f"non-timing benchmark metadata changed for {name}")
        serial_ns = statistics.median(
            [first["serial_median_ns"], second["serial_median_ns"]]
        )
        parallel_ns = statistics.median(
            [first["parallel_median_ns"], second["parallel_median_ns"]]
        )
        speedup = serial_ns / parallel_ns
        case = invariant_payload(first)
        case.update(
            {
                "serial_median_ns": serial_ns,
                "parallel_median_ns": parallel_ns,
                "speedup": speedup,
                "peak_rss_kib": max(first["peak_rss_kib"], second["peak_rss_kib"]),
            }
        )
        result["cases"][name] = case
        speedups.append(speedup)

    geometric_speedup = math.exp(
        sum(math.log(value) for value in speedups) / len(speedups)
    )
    minimum_speedup = min(speedups)
    maximum_difference = max(
        case["max_scaled_difference"] for case in result["cases"].values()
    )
    operators = min(case["operators"] for case in result["cases"].values())
    result.update(
        {
            "geometric_speedup": geometric_speedup,
            "minimum_speedup": minimum_speedup,
            "maximum_scaled_difference": maximum_difference,
            "minimum_operator_count": operators,
            "acceptance_limits": {
                "maximum_scaled_difference": 5.0e-10,
                "minimum_operator_count": 1,
                "geometric_speedup_min": 1.08,
                "per_case_speedup_min": 0.85,
            },
        }
    )
    result["accepted"] = (
        maximum_difference <= 5.0e-10
        and operators >= 1
        and geometric_speedup >= 1.08
        and minimum_speedup >= 0.85
    )
    result["decision_reason"] = (
        "full qualification passed; optional CSR-backed parallel CMG application produced a material four-thread gain"
        if result["accepted"]
        else "qualification passed but numerical, operator, or speedup retention gate was not met"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    restore_candidate()

RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
RESULT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)
cleanup_staging()

run(["git", "config", "user.name", "github-actions[bot]"])
run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
)
run(["git", "add", "-A"])
message = (
    "perf: add optional parallel CMG application plan"
    if result["accepted"]
    else "perf: record parallel CMG application experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
