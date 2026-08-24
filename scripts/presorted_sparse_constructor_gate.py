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
WORKFLOW = Path(".github/workflows/presorted-sparse-constructor.yml")
SCRIPT = Path("scripts/presorted_sparse_constructor_gate.py")
RECORD = Path(".ci/performance/presorted-sparse-constructor-latest.json")
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
    time_path = Path(f"/tmp/cmg-presorted-sparse-{kind}-{tag}.time")
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
    pub(crate) fn from_compact_edges_presorted_or_packed(
        vertex_count: usize,
        mut raw: Vec<Edge>,
    ) -> Result<Self, CmgError> {
        if !endpoint_keys_are_sorted(&raw) {
            sort_compact_edge_endpoints(&mut raw);
        }
        Self::from_endpoint_sorted_raw_edges(vertex_count, raw)
    }
'''
GRAPH_HELPER_MARKER = '''fn endpoint_key(edge: &Edge) -> u64 {
'''
GRAPH_HELPER = '''#[inline]
fn endpoint_keys_are_sorted(raw: &[Edge]) -> bool {
    raw.windows(2)
        .all(|pair| endpoint_key(&pair[0]) <= endpoint_key(&pair[1]))
}

'''
GRAPH_TEST = '''

#[cfg(test)]
mod presorted_sparse_constructor_tests {
    use super::{Edge, Laplacian, endpoint_keys_are_sorted};

    #[test]
    fn presorted_constructor_matches_packed_constructor() {
        let edges = vec![
            Edge::from_internal_parts(0, 1, 1.0).unwrap(),
            Edge::from_internal_parts(1, 2, 2.0).unwrap(),
            Edge::from_internal_parts(2, 3, 3.0).unwrap(),
            Edge::from_internal_parts(3, 4, 4.0).unwrap(),
        ];
        assert!(endpoint_keys_are_sorted(&edges));
        let packed = Laplacian::from_compact_edges(5, edges.clone()).unwrap();
        let presorted =
            Laplacian::from_compact_edges_presorted_or_packed(5, edges).unwrap();
        assert_eq!(presorted, packed);
    }

    #[test]
    fn presorted_constructor_falls_back_for_unsorted_input() {
        let edges = vec![
            Edge::from_internal_parts(2, 3, 3.0).unwrap(),
            Edge::from_internal_parts(0, 1, 1.0).unwrap(),
            Edge::from_internal_parts(1, 2, 2.0).unwrap(),
        ];
        assert!(!endpoint_keys_are_sorted(&edges));
        let packed = Laplacian::from_compact_edges(4, edges.clone()).unwrap();
        let candidate =
            Laplacian::from_compact_edges_presorted_or_packed(4, edges).unwrap();
        assert_eq!(candidate, packed);
    }
}
'''

COARSEN_PREFIX = '''        let mut coarse_edges = Vec::with_capacity(graph.edge_count());
'''
COARSEN_RETURN_PACKED = '''        Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
'''
COARSEN_RETURN_DENSE = '''        if should_use_dense_axis_sort(self.coarse_dimension(), coarse_edges.len()) {
            Laplacian::from_compact_edges_axis_sorted(
                self.coarse_dimension(),
                coarse_edges,
            )
        } else {
            Laplacian::from_compact_edges(self.coarse_dimension(), coarse_edges)
        }
'''
COARSEN_SPARSE_PREFIX = '''        if should_try_presorted_sparse(self.coarse_dimension(), coarse_edges.len()) {
            return Laplacian::from_compact_edges_presorted_or_packed(
                self.coarse_dimension(),
                coarse_edges,
            );
        }
'''
COARSEN_HELPER_MARKER = '''fn validate_prolong_dimensions(
'''
COARSEN_HELPER = '''#[inline]
fn should_try_presorted_sparse(vertex_count: usize, edge_count: usize) -> bool {
    edge_count <= vertex_count
}

'''
COARSEN_TEST = '''

#[cfg(test)]
mod presorted_sparse_router_tests {
    use super::should_try_presorted_sparse;

    #[test]
    fn router_selects_tree_like_edge_counts_only() {
        assert!(should_try_presorted_sparse(1_000_000, 999_999));
        assert!(should_try_presorted_sparse(10, 10));
        assert!(!should_try_presorted_sparse(1_000_000, 1_500_000));
        assert!(!should_try_presorted_sparse(100_000, 800_000));
    }
}
'''


def apply_candidate(graph_source, coarsen_source):
    if graph_source.count(GRAPH_CONSTRUCTOR) != 1:
        raise RuntimeError("packed compact constructor marker changed unexpectedly")
    if graph_source.count(GRAPH_HELPER_MARKER) != 1:
        raise RuntimeError("endpoint-key helper marker changed unexpectedly")
    graph_candidate = graph_source.replace(
        GRAPH_CONSTRUCTOR,
        GRAPH_CONSTRUCTOR_NEW,
        1,
    )
    graph_candidate = graph_candidate.replace(
        GRAPH_HELPER_MARKER,
        GRAPH_HELPER + GRAPH_HELPER_MARKER,
        1,
    )
    if "mod presorted_sparse_constructor_tests" not in graph_candidate:
        graph_candidate += GRAPH_TEST

    if coarsen_source.count(COARSEN_PREFIX) != 1:
        raise RuntimeError("serial contraction allocation marker changed unexpectedly")
    if coarsen_source.count(COARSEN_HELPER_MARKER) != 1:
        raise RuntimeError("coarsen helper insertion marker changed unexpectedly")
    coarsen_candidate = coarsen_source
    if coarsen_candidate.count(COARSEN_RETURN_DENSE) == 1:
        coarsen_candidate = coarsen_candidate.replace(
            COARSEN_RETURN_DENSE,
            COARSEN_SPARSE_PREFIX + COARSEN_RETURN_DENSE,
            1,
        )
    elif coarsen_candidate.count(COARSEN_RETURN_PACKED) == 1:
        coarsen_candidate = coarsen_candidate.replace(
            COARSEN_RETURN_PACKED,
            COARSEN_SPARSE_PREFIX + COARSEN_RETURN_PACKED,
            1,
        )
    else:
        raise RuntimeError("serial contraction return structure changed unexpectedly")
    coarsen_candidate = coarsen_candidate.replace(
        COARSEN_HELPER_MARKER,
        COARSEN_HELPER + COARSEN_HELPER_MARKER,
        1,
    )
    if "mod presorted_sparse_router_tests" not in coarsen_candidate:
        coarsen_candidate += COARSEN_TEST
    return graph_candidate, coarsen_candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    path_contraction = result.get("path_contraction_geometric_time_ratio", 1.0)
    path_hierarchy = result.get("path_hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Presorted sparse-constructor checkpoint — 2026-08-24

- Scanning tree-like coarse edges and skipping packed-key sorting only when exact endpoint order is already monotone was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; unsorted sparse inputs fall back to the original packed sort.
- Path contraction / hierarchy geometric ratios: `{path_contraction:.3f}x` / `{path_hierarchy:.3f}x`.
- Overall hierarchy ratio: `{result.get("hierarchy_geometric_time_ratio", 1.0):.3f}x`; worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/presorted-sparse-constructor-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Presorted sparse-constructor checkpoint — 2026-08-24\n"
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
            "1. Re-profile current contraction and hierarchy after any retained constructor changes.\n"
            "2. Profile parallel setup routing for retained serial constructor optimizations.\n"
            "3. Refresh cumulative retained optimization and memory guidance.\n"
            "4. Run manual 1–32 thread qualification when suitable hardware is available.\n"
        )
    PLAN.write_text(plan)

    block = f'''## Presorted sparse-constructor gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Path contraction / hierarchy ratios: `{path_contraction:.3f}x` / `{path_hierarchy:.3f}x`.
- Evidence: `.ci/performance/presorted-sparse-constructor-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Presorted sparse-constructor gate\n"
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
    "experiment": "presorted-sparse-constructor",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "contraction_cases": {},
    "hierarchy_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-presorted-sparse-baseline"))
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

    candidate = build(Path("/tmp/cmg-presorted-sparse-candidate"))
    specs = (
        ("path-250k", "path", "250000"),
        ("path-1m", "path", "1000000"),
        ("path-2m", "path", "2000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-800k", "dense-worker-firm", "50000"),
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

    path_names = ("path-250k", "path-1m", "path-2m")
    control_names = (
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-800k",
    )
    path_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in path_names
    ]
    path_hierarchy = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in path_names
    ]
    control_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in control_names
    ]
    control_hierarchy = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in control_names
    ]
    all_hierarchy = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["contraction_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["path_contraction_geometric_time_ratio"] = geometric(path_contraction)
    result["path_hierarchy_geometric_time_ratio"] = geometric(path_hierarchy)
    result["hierarchy_geometric_time_ratio"] = geometric(all_hierarchy)
    result["worst_path_contraction_time_ratio"] = max(path_contraction)
    result["worst_path_hierarchy_time_ratio"] = max(path_hierarchy)
    result["worst_control_contraction_time_ratio"] = max(control_contraction)
    result["worst_control_hierarchy_time_ratio"] = max(control_hierarchy)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_path_contraction_count"] = sum(
        value < 1.0 for value in path_contraction
    )
    result["improved_path_hierarchy_count"] = sum(
        value < 1.0 for value in path_hierarchy
    )
    result["acceptance_limits"] = {
        "path_contraction_geometric_time_ratio_max": 0.80,
        "path_hierarchy_geometric_time_ratio_max": 0.99,
        "hierarchy_geometric_time_ratio_max": 0.997,
        "worst_path_contraction_time_ratio_max": 0.90,
        "worst_path_hierarchy_time_ratio_max": 1.01,
        "worst_control_contraction_time_ratio_max": 1.02,
        "worst_control_hierarchy_time_ratio_max": 1.02,
        "worst_peak_rss_ratio_max": 1.01,
        "improved_path_contraction_count_min": 3,
        "improved_path_hierarchy_count_min": 2,
    }
    result["accepted"] = (
        result["path_contraction_geometric_time_ratio"] <= 0.80
        and result["path_hierarchy_geometric_time_ratio"] <= 0.99
        and result["hierarchy_geometric_time_ratio"] <= 0.997
        and result["worst_path_contraction_time_ratio"] <= 0.90
        and result["worst_path_hierarchy_time_ratio"] <= 1.01
        and result["worst_control_contraction_time_ratio"] <= 1.02
        and result["worst_control_hierarchy_time_ratio"] <= 1.02
        and result["worst_peak_rss_ratio"] <= 1.01
        and result["improved_path_contraction_count"] >= 3
        and result["improved_path_hierarchy_count"] >= 2
    )
    result["decision_reason"] = (
        "full qualification passed; already ordered tree-like coarse edges skip sorting while unsorted sparse and all denser controls retain the packed sorter"
        if result["accepted"]
        else "correctness passed, but path, overall hierarchy, control, or memory gates were not all met"
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
    "path_contraction_geometric_time_ratio",
    "path_hierarchy_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "worst_path_contraction_time_ratio",
    "worst_path_hierarchy_time_ratio",
    "worst_control_contraction_time_ratio",
    "worst_control_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_path_contraction_count", 0)
result.setdefault("improved_path_hierarchy_count", 0)
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
    "perf: retain presorted sparse constructor"
    if result.get("accepted", False)
    else "perf: record presorted sparse-constructor experiment"
)
run(["git", "commit", "-m", message])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push presorted sparse-constructor decision")

if result.get("validation") != "success":
    raise SystemExit(1)
