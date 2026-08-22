import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH_PATH = Path("src/graph.rs")
ERROR_PATH = Path("src/error.rs")
GRAPH_ORIGINAL = GRAPH_PATH.read_text()
ERROR_ORIGINAL = ERROR_PATH.read_text()
BASELINE_SHA = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True
).strip()


def run(command, *, env=None, timeout=5400, check=True):
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


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    for binary in ("graph-build", "hierarchy-build"):
        run(
            [
                "cargo",
                "build",
                "--release",
                "--manifest-path",
                "benchmarks/Cargo.toml",
                "--bin",
                binary,
            ],
            env=env,
        )
    return {
        binary: target / "release" / binary
        for binary in ("graph-build", "hierarchy-build")
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-compact-edge-{tag}.time")
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
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    return {
        "median_ns": payloads[0]["median_ns"],
        "peak_rss_kib": int(rss_match.group(1)),
        "metadata": payloads[0],
    }


def compare_case(baseline, candidate, arguments, name, metadata_keys):
    observations = {"baseline": [], "candidate": []}
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
        ("baseline", baseline),
        ("candidate", candidate),
    )
    for index, (label, binary) in enumerate(schedule):
        observations[label].append(
            sample(binary, arguments, f"{name}-{label}-{index}")
        )

    baseline_metadata = observations["baseline"][0]["metadata"]
    candidate_metadata = observations["candidate"][0]["metadata"]
    for key in metadata_keys:
        if baseline_metadata.get(key) != candidate_metadata.get(key):
            raise RuntimeError(
                f"metadata mismatch for {name}.{key}: "
                f"{baseline_metadata.get(key)} != {candidate_metadata.get(key)}"
            )

    baseline_ns = statistics.median(
        item["median_ns"] for item in observations["baseline"]
    )
    candidate_ns = statistics.median(
        item["median_ns"] for item in observations["candidate"]
    )
    baseline_rss = statistics.median(
        item["peak_rss_kib"] for item in observations["baseline"]
    )
    candidate_rss = statistics.median(
        item["peak_rss_kib"] for item in observations["candidate"]
    )
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": candidate_metadata,
        "baseline_samples": observations["baseline"],
        "candidate_samples": observations["candidate"],
    }


result = {
    "schema_version": 1,
    "experiment": "compact-edge-endpoints-u32",
    "baseline_sha": BASELINE_SHA,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "expected_edge_size_bytes": 16,
    "graph_cases": {},
    "hierarchy_cases": {},
}

try:
    analysis_path = Path(".ci/performance/index-layout-analysis.json")
    if analysis_path.exists():
        analysis = json.loads(analysis_path.read_text())
        result["baseline_edge_size_bytes"] = (
            analysis.get("layouts", {}).get("Edge", {}).get("size_bytes")
        )

    baseline = build(Path("/tmp/cmg-compact-edge-baseline"))
    run(["python", "scripts/compact_edge_endpoints.py"])
    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
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
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps"], env=doc_env)
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    candidate = build(Path("/tmp/cmg-compact-edge-candidate"))
    result["validation"] = "success"

    graph_specs = [
        ("unique-1m", ["unique", 1_000_000, 2]),
        ("duplicates-4-1m", ["duplicates-4", 250_000, 2]),
        ("duplicates-16-1.2m", ["duplicates-16", 75_000, 2]),
    ]
    hierarchy_specs = [
        ("worker-firm-600k", ["worker-firm", 200_000, 2]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 2]),
    ]

    time_ratios = []
    memory_ratios = []
    for name, arguments in graph_specs:
        comparison = compare_case(
            baseline["graph-build"],
            candidate["graph-build"],
            arguments,
            name,
            ("case", "scale", "vertices", "raw_edges", "retained_edges"),
        )
        result["graph_cases"][name] = comparison
        time_ratios.append(comparison["candidate_over_baseline_time"])
        memory_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    for name, arguments in hierarchy_specs:
        comparison = compare_case(
            baseline["hierarchy-build"],
            candidate["hierarchy-build"],
            arguments,
            name,
            ("case", "scale", "vertices", "edges"),
        )
        result["hierarchy_cases"][name] = comparison
        time_ratios.append(comparison["candidate_over_baseline_time"])
        memory_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    geometric_time = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    geometric_memory = math.exp(
        sum(math.log(value) for value in memory_ratios) / len(memory_ratios)
    )
    result["geometric_candidate_over_baseline_time"] = geometric_time
    result["worst_candidate_over_baseline_time"] = max(time_ratios)
    result["geometric_candidate_over_baseline_peak_rss"] = geometric_memory
    result["best_candidate_over_baseline_peak_rss"] = min(memory_ratios)
    result["worst_candidate_over_baseline_peak_rss"] = max(memory_ratios)
    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 1.05,
        "per_case_time_ratio_max": 1.12,
        "geometric_peak_rss_ratio_max": 0.995,
        "at_least_one_peak_rss_ratio_max": 0.97,
        "worst_peak_rss_ratio_max": 1.02,
    }
    result["accepted"] = (
        geometric_time <= 1.05
        and max(time_ratios) <= 1.12
        and geometric_memory <= 0.995
        and min(memory_ratios) <= 0.97
        and max(memory_ratios) <= 1.02
    )
    result["decision_reason"] = (
        "full qualification passed; Edge is 16 bytes and measured memory/timing met the gate"
        if result["accepted"]
        else "qualification passed but timing or measured peak-memory evidence missed the gate"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    GRAPH_PATH.write_text(GRAPH_ORIGINAL)
    ERROR_PATH.write_text(ERROR_ORIGINAL)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/compact-edge-endpoints-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("current-next-action heading missing")
status = "retained" if result["accepted"] else "not retained"
timing = result.get("geometric_candidate_over_baseline_time")
memory = result.get("geometric_candidate_over_baseline_peak_rss")
best_memory = result.get("best_candidate_over_baseline_peak_rss")
evidence = ""
if timing is not None and memory is not None and best_memory is not None:
    evidence = (
        f" Geometric time ratio: {timing:.3f}; geometric peak-RSS ratio: "
        f"{memory:.3f}; best peak-RSS ratio: {best_memory:.3f}."
    )
checkpoint = "\n".join(
    [
        "### Compact edge endpoint checkpoint — 2026-08-22",
        "",
        "- Storing retained edge endpoints as checked `u32` values while preserving",
        f"  the public `usize` API was **{status}**.{evidence}",
        "- The candidate uses a typed error for endpoints above `u32::MAX` and",
        "  includes a permanent 16-byte `Edge` layout invariant when retained.",
        f"- Qualification status: `{result['validation']}`.",
        "- Machine-readable evidence:",
        "  `.ci/performance/compact-edge-endpoints-latest.json`.",
        "",
        "",
    ]
)
if "### Compact edge endpoint checkpoint — 2026-08-22" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

for staging in (
    "scripts/compact_edge_endpoints.py",
    "scripts/compact_edge_endpoint_gate.py",
    ".github/workflows/compact-edge-endpoints.yml",
):
    Path(staging).unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

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
    "perf: compact retained edge endpoints"
    if result["accepted"]
    else "perf: record compact edge endpoint experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
