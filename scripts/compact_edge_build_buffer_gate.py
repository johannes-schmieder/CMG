import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH_PATH = Path("src/graph.rs")
GRAPH_ORIGINAL = GRAPH_PATH.read_text()
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
    time_path = Path(f"/tmp/cmg-build-buffer-{tag}.time")
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

    reference = observations["baseline"][0]["metadata"]
    for version in ("baseline", "candidate"):
        for observation in observations[version]:
            metadata = observation["metadata"]
            for key in metadata_keys:
                if metadata.get(key) != reference.get(key):
                    raise RuntimeError(
                        f"metadata mismatch for {name}.{key}: "
                        f"{metadata.get(key)} != {reference.get(key)}"
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
        "metadata": observations["candidate"][0]["metadata"],
        "baseline_samples": observations["baseline"],
        "candidate_samples": observations["candidate"],
    }


def geometric_mean(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


result = {
    "schema_version": 1,
    "experiment": "compact-edge-build-buffer",
    "baseline_sha": BASELINE_SHA,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "graph_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-build-buffer-baseline"))
    run(["python", "scripts/compact_edge_build_buffer.py"])
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
    candidate = build(Path("/tmp/cmg-build-buffer-candidate"))
    result["validation"] = "success"

    graph_specs = [
        ("unique-1m", ["unique", 1_000_000, 2]),
        ("duplicates-4-1m", ["duplicates-4", 250_000, 2]),
        ("duplicates-16-1.2m", ["duplicates-16", 75_000, 2]),
        ("coarse-collisions-1.2m", ["coarse-collisions", 75_000, 2]),
    ]
    hierarchy_specs = [
        ("worker-firm-600k", ["worker-firm", 200_000, 2]),
        ("dense-worker-firm-800k", ["dense-worker-firm", 50_000, 2]),
    ]

    graph_time_ratios = []
    graph_memory_ratios = []
    hierarchy_time_ratios = []
    hierarchy_memory_ratios = []

    for name, arguments in graph_specs:
        comparison = compare_case(
            baseline["graph-build"],
            candidate["graph-build"],
            arguments,
            name,
            ("case", "scale", "vertices", "raw_edges", "retained_edges"),
        )
        result["graph_cases"][name] = comparison
        graph_time_ratios.append(comparison["candidate_over_baseline_time"])
        graph_memory_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    for name, arguments in hierarchy_specs:
        comparison = compare_case(
            baseline["hierarchy-build"],
            candidate["hierarchy-build"],
            arguments,
            name,
            ("case", "scale", "vertices", "edges"),
        )
        result["hierarchy_cases"][name] = comparison
        hierarchy_time_ratios.append(comparison["candidate_over_baseline_time"])
        hierarchy_memory_ratios.append(comparison["candidate_over_baseline_peak_rss"])

    graph_time_geomean = geometric_mean(graph_time_ratios)
    graph_memory_geomean = geometric_mean(graph_memory_ratios)
    hierarchy_time_geomean = geometric_mean(hierarchy_time_ratios)
    hierarchy_memory_geomean = geometric_mean(hierarchy_memory_ratios)
    result["graph_time_geometric_mean_ratio"] = graph_time_geomean
    result["graph_peak_rss_geometric_mean_ratio"] = graph_memory_geomean
    result["hierarchy_time_geometric_mean_ratio"] = hierarchy_time_geomean
    result["hierarchy_peak_rss_geometric_mean_ratio"] = hierarchy_memory_geomean
    result["best_candidate_over_baseline_peak_rss"] = min(
        graph_memory_ratios + hierarchy_memory_ratios
    )
    result["worst_candidate_over_baseline_peak_rss"] = max(
        graph_memory_ratios + hierarchy_memory_ratios
    )
    result["worst_candidate_over_baseline_time"] = max(
        graph_time_ratios + hierarchy_time_ratios
    )
    result["acceptance_limits"] = {
        "graph_time_geometric_mean_max": 1.03,
        "hierarchy_time_geometric_mean_max": 1.03,
        "per_case_time_max": 1.10,
        "graph_peak_rss_geometric_mean_max": 0.94,
        "hierarchy_peak_rss_geometric_mean_max": 0.98,
        "worst_peak_rss_max": 1.01,
    }
    result["accepted"] = (
        graph_time_geomean <= 1.03
        and hierarchy_time_geomean <= 1.03
        and max(graph_time_ratios + hierarchy_time_ratios) <= 1.10
        and graph_memory_geomean <= 0.94
        and hierarchy_memory_geomean <= 0.98
        and max(graph_memory_ratios + hierarchy_memory_ratios) <= 1.01
    )
    result["decision_reason"] = (
        "full qualification passed; compact in-place construction reduced scratch memory without a timing regression"
        if result["accepted"]
        else "qualification passed but graph/hierarchy timing or peak-memory evidence missed the gate"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    GRAPH_PATH.write_text(GRAPH_ORIGINAL)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(".ci/performance/compact-edge-build-buffer-latest.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("current-next-action heading missing")
status = "retained" if result["accepted"] else "not retained"
graph_time = result.get("graph_time_geometric_mean_ratio")
graph_memory = result.get("graph_peak_rss_geometric_mean_ratio")
hierarchy_time = result.get("hierarchy_time_geometric_mean_ratio")
hierarchy_memory = result.get("hierarchy_peak_rss_geometric_mean_ratio")
evidence = ""
if None not in (graph_time, graph_memory, hierarchy_time, hierarchy_memory):
    evidence = (
        f" Graph time/RSS ratios: {graph_time:.3f}/{graph_memory:.3f}; "
        f"hierarchy time/RSS ratios: {hierarchy_time:.3f}/{hierarchy_memory:.3f}."
    )
checkpoint = "\n".join(
    [
        "### Compact graph-build buffer checkpoint — 2026-08-22",
        "",
        "- Collecting validated edges directly into the retained 16-byte layout and",
        f"  compacting duplicate pairs in place was **{status}**.{evidence}",
        "- Endpoint ordering, weight ordering, compensated duplicate summation, and",
        "  all graph/hierarchy invariants were required to remain unchanged.",
        f"- Qualification status: `{result['validation']}`.",
        "- Machine-readable evidence:",
        "  `.ci/performance/compact-edge-build-buffer-latest.json`.",
        "",
        "",
    ]
)
if "### Compact graph-build buffer checkpoint — 2026-08-22" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

for staging in (
    "scripts/compact_edge_build_buffer.py",
    "scripts/compact_edge_build_buffer_gate.py",
    ".github/workflows/compact-edge-build-buffer.yml",
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
    "perf: compact graph construction buffer"
    if result["accepted"]
    else "perf: record compact graph-build buffer experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
