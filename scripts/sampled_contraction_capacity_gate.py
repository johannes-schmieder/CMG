import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/coarsen.rs")
WORKFLOW = Path(".github/workflows/sampled-contraction-capacity.yml")
SCRIPT = Path("scripts/sampled_contraction_capacity_gate.py")
RECORD = Path(".ci/performance/sampled-contraction-capacity-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
        )
    return completed


def build_benchmarks(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "time": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-capacity-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            time_path,
            binary,
            *arguments,
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
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare(binary_key, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[binary_key]),
        ("candidate", candidate[binary_key]),
        ("candidate", candidate[binary_key]),
        ("baseline", baseline[binary_key]),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{binary_key}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    stable = ("case", "scale", "vertices", "edges", "repetitions")
    if binary_key == "allocation":
        stable += ("levels", "hierarchy_matrix_nonzeros", "max_post_drop_delta_bytes")
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {binary_key} metadata for {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": statistics.median(item["median_ns"] for item in baseline_samples),
        "candidate_median_ns": statistics.median(item["median_ns"] for item in candidate_samples),
        "baseline_peak_rss_kib": max(item["peak_rss_kib"] for item in baseline_samples),
        "candidate_peak_rss_kib": max(item["peak_rss_kib"] for item in candidate_samples),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    if binary_key == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_value = statistics.median(item[field] for item in baseline_samples)
            candidate_value = statistics.median(item[field] for item in candidate_samples)
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    return result


OLD_ALLOCATION = "        let mut coarse_edges = Vec::with_capacity(graph.edge_count());\n"
NEW_ALLOCATION = (
    "        let mut coarse_edges =\n"
    "            Vec::with_capacity(self.estimated_contraction_capacity(graph));\n"
)
METHOD_MARKER = "    fn validate_contract_graph(&self, graph: &Laplacian) -> Result<(), CmgError> {\n"
METHOD = '''    fn estimated_contraction_capacity(&self, graph: &Laplacian) -> usize {
        const SAMPLE_LIMIT: usize = 8_192;
        let edge_count = graph.edge_count();
        if edge_count == 0 {
            return 0;
        }

        let sample_count = edge_count.min(SAMPLE_LIMIT);
        let surviving = (0..sample_count)
            .filter(|&sample_index| {
                let edge_index = sample_index.saturating_mul(edge_count) / sample_count;
                let edge = graph.edges()[edge_index];
                self.label_at(edge.u()) != self.label_at(edge.v())
            })
            .count();
        let estimated = ((edge_count as u128 * surviving as u128)
            .div_ceil(sample_count as u128)) as usize;
        let safety_margin = estimated.div_ceil(8).max(64);
        estimated.saturating_add(safety_margin).min(edge_count)
    }

'''
TEST_MODULE = '''

#[cfg(test)]
mod sampled_contraction_capacity_tests {
    use super::Aggregation;
    use crate::Laplacian;

    #[test]
    fn sampled_capacity_reduces_path_overallocation_without_growth() {
        let vertices = 4_001;
        let graph = Laplacian::from_edges(
            vertices,
            (0..vertices - 1).map(|vertex| (vertex, vertex + 1, 1.0)),
        )
        .unwrap();
        let labels: Vec<_> = (0..vertices).map(|vertex| vertex / 4).collect();
        let aggregation = Aggregation::new(labels, vertices.div_ceil(4)).unwrap();
        let capacity = aggregation.estimated_contraction_capacity(&graph);
        let survivors = graph
            .edges()
            .iter()
            .filter(|edge| aggregation.label_at(edge.u()) != aggregation.label_at(edge.v()))
            .count();

        assert!(capacity >= survivors);
        assert!(capacity < graph.edge_count() / 2);
        assert_eq!(aggregation.contract(&graph).unwrap().edge_count(), survivors);
    }

    #[test]
    fn sampled_capacity_is_bounded_for_all_internal_edges() {
        let graph = Laplacian::from_edges(
            1_000,
            (0..999).map(|vertex| (vertex, vertex + 1, 1.0)),
        )
        .unwrap();
        let aggregation = Aggregation::new(vec![0; 1_000], 1).unwrap();
        assert!(aggregation.estimated_contraction_capacity(&graph) <= 64);
        assert_eq!(aggregation.contract(&graph).unwrap().edge_count(), 0);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_ALLOCATION) != 1:
        raise RuntimeError("expected one serial contraction allocation site")
    candidate = source.replace(OLD_ALLOCATION, NEW_ALLOCATION, 1)
    if METHOD_MARKER not in candidate:
        raise RuntimeError("contract validation marker missing")
    candidate = candidate.replace(METHOD_MARKER, METHOD + METHOD_MARKER, 1)
    if "mod sampled_contraction_capacity_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def update_documents(result):
    retained = result.get("accepted", False)
    decision = "retained" if retained else "not retained"
    time_ratio = result.get("time_geometric_ratio", 1.0)
    peak_ratio = result.get("allocation_geometric_additional_peak_ratio", 1.0)
    rss_ratio = result.get("time_worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Sampled contraction-capacity checkpoint — 2026-08-23

- Deterministic sampled survivor capacity was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy-build ratio: `{time_ratio:.3f}x`.
- Geometric exact additional-peak ratio: `{peak_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/sampled-contraction-capacity-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Sampled contraction-capacity checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Sampled contraction-capacity gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy-build ratio: `{time_ratio:.3f}x`.
- Exact additional-peak ratio: `{peak_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/sampled-contraction-capacity-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Sampled contraction-capacity gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_source = SOURCE.read_text()
result = {
    "schema_version": 1,
    "experiment": "sampled-contraction-survivor-capacity",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "time_cases": {},
    "allocation_cases": {},
}

try:
    baseline = build_benchmarks(Path("/tmp/cmg-capacity-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
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
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
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
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build_benchmarks(Path("/tmp/cmg-capacity-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "2"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "2"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "2"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2"]),
    )
    for name, arguments in specs:
        result["time_cases"][name] = compare("time", baseline, candidate, arguments, name)
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    time_ratios = [case["candidate_over_baseline_time"] for case in result["time_cases"].values()]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"] for case in result["time_cases"].values()
    ]
    allocation_ratios = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    result["time_geometric_ratio"] = math.exp(
        sum(math.log(value) for value in time_ratios) / len(time_ratios)
    )
    result["time_worst_ratio"] = max(time_ratios)
    result["time_worst_peak_rss_ratio"] = max(rss_ratios)
    result["allocation_geometric_additional_peak_ratio"] = math.exp(
        sum(math.log(value) for value in allocation_ratios) / len(allocation_ratios)
    )
    result["allocation_worst_additional_peak_ratio"] = max(allocation_ratios)
    result["allocation_worst_retained_ratio"] = max(retained_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["acceptance_limits"] = {
        "time_geometric_ratio_max": 1.02,
        "time_worst_ratio_max": 1.05,
        "time_worst_peak_rss_ratio_max": 1.03,
        "allocation_geometric_additional_peak_ratio_max": 0.98,
        "path_additional_peak_ratio_max": 0.92,
        "worker_firm_additional_peak_ratio_max": 0.97,
        "allocation_worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    path_allocation = result["allocation_cases"]["path-1m"][
        "candidate_over_baseline_median_additional_peak_bytes"
    ]
    worker_allocation = result["allocation_cases"]["worker-firm-1.5m"][
        "candidate_over_baseline_median_additional_peak_bytes"
    ]
    result["accepted"] = (
        result["time_geometric_ratio"] <= 1.02
        and result["time_worst_ratio"] <= 1.05
        and result["time_worst_peak_rss_ratio"] <= 1.03
        and result["allocation_geometric_additional_peak_ratio"] <= 0.98
        and path_allocation <= 0.92
        and worker_allocation <= 0.97
        and result["allocation_worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; sampled capacity reduced temporary hierarchy memory without a material timing regression"
        if result["accepted"]
        else "qualification passed, but timing or exact/process memory gates were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

result.setdefault("time_geometric_ratio", 1.0)
result.setdefault("time_worst_ratio", 1.0)
result.setdefault("time_worst_peak_rss_ratio", 1.0)
result.setdefault("allocation_geometric_additional_peak_ratio", 1.0)
result.setdefault("allocation_worst_additional_peak_ratio", 1.0)
result.setdefault("allocation_worst_retained_ratio", 1.0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
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
    "perf: retain sampled contraction capacity"
    if result.get("accepted", False)
    else "perf: record sampled contraction-capacity experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push sampled contraction-capacity decision")
