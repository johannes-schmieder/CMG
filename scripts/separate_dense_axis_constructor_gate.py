import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
GRAPH = Path("src/graph.rs")
COARSEN = Path("src/coarsen.rs")
WORKFLOW = Path(".github/workflows/separate-dense-axis-constructor.yml")
SCRIPT = Path("scripts/separate_dense_axis_constructor_gate.py")
RECORD = Path(".ci/performance/separate-dense-axis-constructor-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, env=None, timeout=7200, check=True):
    command = [str(item) for item in command]
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
            "contraction-subphase-profile",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "hierarchy": release / "hierarchy-build",
        "contraction": release / "contraction-subphase-profile",
    }


def sample(kind, binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-separate-axis-{kind}-{tag}.time")
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
    if kind == "contraction":
        payloads = [payload for payload in payloads if payload.get("record") == "case"]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected {kind} benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing from /usr/bin/time output")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(
            kind,
            binary,
            arguments,
            f"{name}-{label}-{index}",
        )
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        ("case", "scale", "vertices", "edges", "repetitions")
        if kind == "hierarchy"
        else (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "profiled_levels",
        )
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")

    metric = "median_ns" if kind == "hierarchy" else "production_total_ns"
    baseline_metric = statistics.median(item[metric] for item in baseline_samples)
    candidate_metric = statistics.median(item[metric] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    return {
        "arguments": arguments,
        "metric": metric,
        "baseline_metric": baseline_metric,
        "candidate_metric": candidate_metric,
        "candidate_over_baseline_time": candidate_metric / baseline_metric,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable},
    }


GRAPH_CONSTRUCTOR = '''    pub(crate) fn from_compact_edges(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_edge_endpoints(&mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
GRAPH_CONSTRUCTOR_NEW = GRAPH_CONSTRUCTOR + '''
    pub(crate) fn from_compact_edges_axis_sorted(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        sort_compact_endpoint_axes(&mut raw);
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
GRAPH_SORT_HELPER = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
GRAPH_SORT_HELPER_NEW = GRAPH_SORT_HELPER + '''
fn sort_compact_endpoint_axes(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(|edge| edge.u);
    let mut start = 0;
    while start < raw.len() {
        let first_endpoint = raw[start].u;
        let mut end = start + 1;
        while end < raw.len() && raw[end].u == first_endpoint {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by_key(|edge| edge.v);
        }
        start = end;
    }
}
'''
GRAPH_TEST = '''

#[cfg(test)]
mod separate_dense_axis_constructor_tests {
    use super::{Edge, Laplacian};

    #[test]
    fn axis_constructor_matches_packed_constructor_exactly() {
        let mut edges = Vec::new();
        for index in 0..8_192_usize {
            let left = (37 * index + 11) % 2_003;
            let mut right = (97 * index + 29) % 2_003;
            if right == left {
                right = (right + 1) % 2_003;
            }
            for duplicate in 0..3_usize {
                let weight = 0.25 + ((index + 19 * duplicate) % 127) as f64 / 64.0;
                edges.push(Edge::from_internal_parts(left, right, weight).unwrap());
            }
        }
        edges.reverse();
        let packed = Laplacian::from_compact_edges(2_003, edges.clone()).unwrap();
        let axis = Laplacian::from_compact_edges_axis_sorted(2_003, edges).unwrap();
        assert_eq!(axis, packed);
    }
}
'''

COARSEN_RETURN = '''        Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
'''
COARSEN_RETURN_NEW = '''        if should_use_dense_axis_sort(self.coarse_dimension(), coarse_edges.len()) {
            Laplacian::from_compact_edges_axis_sorted(
                self.coarse_dimension(),
                coarse_edges,
            )
        } else {
            Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
        }
'''
COARSEN_HELPER_MARKER = '''fn validate_prolong_dimensions(
'''
COARSEN_HELPER = '''const DENSE_AXIS_SORT_MIN_EDGES: usize = 500_000;
const DENSE_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_dense_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= DENSE_AXIS_SORT_MIN_EDGES
        && edge_count
            >= vertex_count.saturating_mul(DENSE_AXIS_SORT_MIN_EDGES_PER_VERTEX)
}

'''
COARSEN_TEST = '''

#[cfg(test)]
mod dense_axis_sort_router_tests {
    use super::should_use_dense_axis_sort;

    #[test]
    fn router_selects_only_large_dense_coarse_graphs() {
        assert!(!should_use_dense_axis_sort(1_000_000, 1_500_000));
        assert!(!should_use_dense_axis_sort(1_500_000, 2_250_000));
        assert!(!should_use_dense_axis_sort(50_000, 400_000));
        assert!(should_use_dense_axis_sort(75_000, 600_000));
        assert!(should_use_dense_axis_sort(100_000, 800_000));
    }
}
'''


def apply_candidate(graph_source, coarsen_source):
    if graph_source.count(GRAPH_CONSTRUCTOR) != 1:
        raise RuntimeError("compact graph constructor marker changed unexpectedly")
    if graph_source.count(GRAPH_SORT_HELPER) != 1:
        raise RuntimeError("compact graph sort helper marker changed unexpectedly")
    graph_candidate = graph_source.replace(
        GRAPH_CONSTRUCTOR,
        GRAPH_CONSTRUCTOR_NEW,
        1,
    )
    graph_candidate = graph_candidate.replace(
        GRAPH_SORT_HELPER,
        GRAPH_SORT_HELPER_NEW,
        1,
    )
    if "mod separate_dense_axis_constructor_tests" not in graph_candidate:
        graph_candidate += GRAPH_TEST

    if coarsen_source.count(COARSEN_RETURN) != 1:
        raise RuntimeError("serial contraction return marker changed unexpectedly")
    if coarsen_source.count(COARSEN_HELPER_MARKER) != 1:
        raise RuntimeError("coarsen helper insertion marker changed unexpectedly")
    coarsen_candidate = coarsen_source.replace(
        COARSEN_RETURN,
        COARSEN_RETURN_NEW,
        1,
    )
    coarsen_candidate = coarsen_candidate.replace(
        COARSEN_HELPER_MARKER,
        COARSEN_HELPER + COARSEN_HELPER_MARKER,
        1,
    )
    if "mod dense_axis_sort_router_tests" not in coarsen_candidate:
        coarsen_candidate += COARSEN_TEST
    return graph_candidate, coarsen_candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    active_contraction = result.get("active_contraction_geometric_time_ratio", 1.0)
    active_hierarchy = result.get("active_hierarchy_geometric_time_ratio", 1.0)
    overall_hierarchy = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Separate dense axis-constructor checkpoint — 2026-08-24

- Routing only qualified dense coarse graphs to a separate axis-sorted constructor was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; packed controls use the original constructor unchanged.
- Active dense contraction / hierarchy ratios: `{active_contraction:.3f}x` / `{active_hierarchy:.3f}x`.
- Overall hierarchy ratio: `{overall_hierarchy:.3f}x`; worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/separate-dense-axis-constructor-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Separate dense axis-constructor checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    if marker in plan:
        prefix, _, _ = plan.partition(marker)
        plan = prefix + marker + (
            "1. Re-profile contraction and hierarchy if the separate dense constructor is retained.\n"
            "2. Otherwise close endpoint-axis routing and test a sorted-input fast path for path-like levels.\n"
            "3. Refresh cumulative retained optimization and memory guidance.\n"
            "4. Run manual 1–32 thread qualification when suitable hardware is available.\n"
        )
    PLAN.write_text(plan)

    block = f'''## Separate dense axis-constructor gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Active dense contraction / hierarchy ratios: `{active_contraction:.3f}x` / `{active_hierarchy:.3f}x`.
- Overall hierarchy ratio: `{overall_hierarchy:.3f}x`.
- Evidence: `.ci/performance/separate-dense-axis-constructor-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Separate dense axis-constructor gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_graph = GRAPH.read_text()
baseline_coarsen = COARSEN.read_text()
result = {
    "schema_version": 1,
    "experiment": "separate-dense-axis-constructor",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "contraction_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-separate-axis-baseline"))
    graph_candidate, coarsen_candidate = apply_candidate(
        baseline_graph,
        baseline_coarsen,
    )
    GRAPH.write_text(graph_candidate)
    COARSEN.write_text(coarsen_candidate)

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run([
        "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml",
        "--all", "--", "--check",
    ])
    run([
        "cargo", "clippy", "--all-targets", "--all-features",
        "--", "-D", "warnings",
    ])
    run([
        "cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml",
        "--all-targets", "--", "-D", "warnings",
    ])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-separate-axis-candidate"))
    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-2.25m", "worker-firm", "750000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-400k", "dense-worker-firm", "25000"),
        ("dense-worker-firm-600k", "dense-worker-firm", "37500"),
        ("dense-worker-firm-800k", "dense-worker-firm", "50000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
    for name, case, scale in specs:
        result["contraction_cases"][name] = compare(
            "contraction",
            baseline,
            candidate,
            [case, scale, "5", "comparison"],
            name,
        )
        result["hierarchy_cases"][name] = compare(
            "hierarchy",
            baseline,
            candidate,
            [case, scale, "5"],
            name,
        )

    contraction_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["contraction_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    active_names = (
        "dense-worker-firm-600k",
        "dense-worker-firm-800k",
        "dense-worker-firm-1.6m",
    )
    control_names = (
        "path-1m",
        "worker-firm-1.5m",
        "worker-firm-2.25m",
        "worker-firm-3m",
        "dense-worker-firm-400k",
    )
    active_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    active_hierarchy = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    control_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in control_names
    ]
    control_hierarchy = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in control_names
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["contraction_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["contraction_geometric_time_ratio"] = geometric(contraction_ratios)
    result["active_contraction_geometric_time_ratio"] = geometric(active_contraction)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["active_hierarchy_geometric_time_ratio"] = geometric(active_hierarchy)
    result["worst_control_contraction_time_ratio"] = max(control_contraction)
    result["worst_control_hierarchy_time_ratio"] = max(control_hierarchy)
    result["worst_contraction_time_ratio"] = max(contraction_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_active_contraction_count"] = sum(
        value < 1.0 for value in active_contraction
    )
    result["improved_active_hierarchy_count"] = sum(
        value < 1.0 for value in active_hierarchy
    )
    result["acceptance_limits"] = {
        "active_contraction_geometric_time_ratio_max": 0.985,
        "contraction_geometric_time_ratio_max": 0.995,
        "active_hierarchy_geometric_time_ratio_max": 0.985,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_control_contraction_time_ratio_max": 1.03,
        "worst_control_hierarchy_time_ratio_max": 1.02,
        "worst_contraction_time_ratio_max": 1.03,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_active_contraction_count_min": 3,
        "improved_active_hierarchy_count_min": 3,
    }
    result["accepted"] = (
        result["active_contraction_geometric_time_ratio"] <= 0.985
        and result["contraction_geometric_time_ratio"] <= 0.995
        and result["active_hierarchy_geometric_time_ratio"] <= 0.985
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_control_contraction_time_ratio"] <= 1.03
        and result["worst_control_hierarchy_time_ratio"] <= 1.02
        and result["worst_contraction_time_ratio"] <= 1.03
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_active_contraction_count"] >= 3
        and result["improved_active_hierarchy_count"] >= 3
    )
    result["decision_reason"] = (
        "full qualification passed; dense axis sorting is isolated in a separate constructor and packed controls retain their original sort implementation"
        if result["accepted"]
        else "correctness passed, but active, whole-workload, control, hierarchy, or memory gates were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    GRAPH.write_text(baseline_graph)
    COARSEN.write_text(baseline_coarsen)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "contraction_geometric_time_ratio",
    "active_contraction_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "active_hierarchy_geometric_time_ratio",
    "worst_control_contraction_time_ratio",
    "worst_control_hierarchy_time_ratio",
    "worst_contraction_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_active_contraction_count", 0)
result.setdefault("improved_active_hierarchy_count", 0)
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
run([
    "git", "config", "user.email",
    "41898282+github-actions[bot]@users.noreply.github.com",
])
run(["git", "add", "-A"])
message = (
    "perf: retain separate dense axis constructor"
    if result.get("accepted", False)
    else "perf: record separate dense axis-constructor experiment"
)
run(["git", "commit", "-m", message])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push separate dense axis-constructor decision")

if result.get("validation") != "success":
    raise SystemExit(1)
