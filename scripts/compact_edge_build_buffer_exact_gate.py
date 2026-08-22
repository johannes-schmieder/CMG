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
    for binary in ("graph-build", "hierarchy-alloc"):
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
        for binary in ("graph-build", "hierarchy-alloc")
    }


def timed_sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-exact-buffer-{tag}.time")
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
    payload = payloads[0]
    payload["process_peak_rss_kib"] = int(rss_match.group(1))
    return payload


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
            timed_sample(binary, arguments, f"{name}-{label}-{index}")
        )

    reference = observations["baseline"][0]
    for version in ("baseline", "candidate"):
        for observation in observations[version]:
            for key in metadata_keys:
                if observation.get(key) != reference.get(key):
                    raise RuntimeError(
                        f"metadata mismatch for {name}.{key}: "
                        f"{observation.get(key)} != {reference.get(key)}"
                    )

    result = {
        "arguments": arguments,
        "baseline_samples": observations["baseline"],
        "candidate_samples": observations["candidate"],
    }
    metric_keys = ["median_ns", "process_peak_rss_kib"]
    if "median_additional_peak_bytes" in reference:
        metric_keys.extend(
            [
                "median_additional_peak_bytes",
                "median_retained_bytes",
                "max_post_drop_delta_bytes",
            ]
        )
    for key in metric_keys:
        baseline_value = statistics.median(
            observation[key] for observation in observations["baseline"]
        )
        candidate_value = statistics.median(
            observation[key] for observation in observations["candidate"]
        )
        result[f"baseline_{key}"] = baseline_value
        result[f"candidate_{key}"] = candidate_value
        if baseline_value != 0:
            result[f"candidate_over_baseline_{key}"] = (
                candidate_value / baseline_value
            )
        elif candidate_value == 0:
            result[f"candidate_over_baseline_{key}"] = 1.0
        else:
            result[f"candidate_over_baseline_{key}"] = float("inf")
    return result


def geometric_mean(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


result = {
    "schema_version": 1,
    "experiment": "compact-edge-build-buffer-exact-allocation",
    "baseline_sha": BASELINE_SHA,
    "accepted": False,
    "validation": "not_run",
    "decision_reason": "",
    "graph_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-exact-buffer-baseline"))
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
    run(["cargo", "doc", "--no-deps"], env=doc_env)
    run(["cargo", "test", "--all-targets"])
    run(["cargo", "test", "--all-targets", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    candidate = build(Path("/tmp/cmg-exact-buffer-candidate"))
    result["validation"] = "success"

    graph_specs = [
        ("unique-1m", ["unique", 1_000_000, 2]),
        ("duplicates-4-1m", ["duplicates-4", 250_000, 2]),
        ("coarse-collisions-1.2m", ["coarse-collisions", 75_000, 2]),
    ]
    hierarchy_specs = [
        ("path-100k", ["path", 100_000, 3]),
        ("worker-firm-300k", ["worker-firm", 100_000, 3]),
        ("dense-worker-firm-480k", ["dense-worker-firm", 30_000, 3]),
    ]

    graph_time = []
    graph_rss = []
    for name, arguments in graph_specs:
        comparison = compare_case(
            baseline["graph-build"],
            candidate["graph-build"],
            arguments,
            name,
            ("case", "scale", "vertices", "raw_edges", "retained_edges"),
        )
        result["graph_cases"][name] = comparison
        graph_time.append(comparison["candidate_over_baseline_median_ns"])
        graph_rss.append(
            comparison["candidate_over_baseline_process_peak_rss_kib"]
        )

    hierarchy_time = []
    hierarchy_rss = []
    hierarchy_peak_alloc = []
    hierarchy_retained = []
    post_drop_values = []
    for name, arguments in hierarchy_specs:
        comparison = compare_case(
            baseline["hierarchy-alloc"],
            candidate["hierarchy-alloc"],
            arguments,
            name,
            (
                "case",
                "scale",
                "vertices",
                "edges",
                "levels",
                "hierarchy_matrix_nonzeros",
            ),
        )
        result["hierarchy_cases"][name] = comparison
        hierarchy_time.append(comparison["candidate_over_baseline_median_ns"])
        hierarchy_rss.append(
            comparison["candidate_over_baseline_process_peak_rss_kib"]
        )
        hierarchy_peak_alloc.append(
            comparison[
                "candidate_over_baseline_median_additional_peak_bytes"
            ]
        )
        hierarchy_retained.append(
            comparison["candidate_over_baseline_median_retained_bytes"]
        )
        post_drop_values.extend(
            [
                comparison["baseline_max_post_drop_delta_bytes"],
                comparison["candidate_max_post_drop_delta_bytes"],
            ]
        )

    result["graph_time_geometric_mean_ratio"] = geometric_mean(graph_time)
    result["graph_peak_rss_geometric_mean_ratio"] = geometric_mean(graph_rss)
    result["hierarchy_time_geometric_mean_ratio"] = geometric_mean(
        hierarchy_time
    )
    result["hierarchy_process_peak_rss_geometric_mean_ratio"] = geometric_mean(
        hierarchy_rss
    )
    result["hierarchy_additional_peak_geometric_mean_ratio"] = geometric_mean(
        hierarchy_peak_alloc
    )
    result["hierarchy_retained_geometric_mean_ratio"] = geometric_mean(
        hierarchy_retained
    )
    result["worst_time_ratio"] = max(graph_time + hierarchy_time)
    result["worst_exact_peak_allocation_ratio"] = max(hierarchy_peak_alloc)
    result["worst_retained_allocation_ratio"] = max(hierarchy_retained)
    result["max_post_drop_delta_bytes"] = max(post_drop_values)
    result["acceptance_limits"] = {
        "graph_time_geometric_mean_max": 1.03,
        "graph_peak_rss_geometric_mean_max": 0.94,
        "hierarchy_time_geometric_mean_max": 1.03,
        "per_case_time_max": 1.10,
        "hierarchy_additional_peak_geometric_mean_max": 0.97,
        "worst_exact_peak_allocation_max": 1.001,
        "worst_retained_allocation_max": 1.001,
        "max_post_drop_delta_bytes": 0,
        "hierarchy_process_peak_rss": "reported, not a hard gate",
    }
    result["accepted"] = (
        result["graph_time_geometric_mean_ratio"] <= 1.03
        and result["graph_peak_rss_geometric_mean_ratio"] <= 0.94
        and result["hierarchy_time_geometric_mean_ratio"] <= 1.03
        and result["worst_time_ratio"] <= 1.10
        and result["hierarchy_additional_peak_geometric_mean_ratio"] <= 0.97
        and result["worst_exact_peak_allocation_ratio"] <= 1.001
        and result["worst_retained_allocation_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; exact hierarchy allocation, graph RSS, and timing all improved"
        if result["accepted"]
        else "qualification passed but exact allocation or timing evidence missed the retention gate"
    )
except Exception as error:
    result["decision_reason"] = f"experiment failed: {error}"
    result["error"] = repr(error)
    print(result["decision_reason"], flush=True)

if not result["accepted"]:
    GRAPH_PATH.write_text(GRAPH_ORIGINAL)
    run(["cargo", "fmt", "--all"], check=False)

record = Path(
    ".ci/performance/compact-edge-build-buffer-exact-latest.json"
)
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("current-next-action heading missing")
status = "retained" if result["accepted"] else "not retained"
graph_time_ratio = result.get("graph_time_geometric_mean_ratio")
graph_rss_ratio = result.get("graph_peak_rss_geometric_mean_ratio")
hierarchy_time_ratio = result.get("hierarchy_time_geometric_mean_ratio")
exact_peak_ratio = result.get(
    "hierarchy_additional_peak_geometric_mean_ratio"
)
evidence = ""
if None not in (
    graph_time_ratio,
    graph_rss_ratio,
    hierarchy_time_ratio,
    exact_peak_ratio,
):
    evidence = (
        f" Graph time/RSS: {graph_time_ratio:.3f}/{graph_rss_ratio:.3f}; "
        f"hierarchy time/exact peak allocation: "
        f"{hierarchy_time_ratio:.3f}/{exact_peak_ratio:.3f}."
    )
checkpoint = "\n".join(
    [
        "### Exact-allocation graph buffer checkpoint — 2026-08-22",
        "",
        "- The compact in-place graph-construction buffer was requalified with the",
        f"  benchmark-only exact hierarchy allocation tracker and was **{status}**.{evidence}",
        "- Process peak RSS remains recorded as supporting evidence; the hard memory",
        "  gate uses exact additional live requested bytes during hierarchy setup.",
        f"- Qualification status: `{result['validation']}`.",
        "- Machine-readable evidence:",
        "  `.ci/performance/compact-edge-build-buffer-exact-latest.json`.",
        "",
        "",
    ]
)
if "### Exact-allocation graph buffer checkpoint — 2026-08-22" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

for staging in (
    "scripts/compact_edge_build_buffer.py",
    "scripts/compact_edge_build_buffer_exact_gate.py",
    ".github/workflows/compact-edge-build-buffer-exact.yml",
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
    else "perf: record exact-allocation graph buffer experiment"
)
run(["git", "commit", "-m", message])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
