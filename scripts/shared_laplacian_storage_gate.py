import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/shared-laplacian-storage.yml")
SCRIPT = Path("scripts/shared_laplacian_storage_gate.py")
RECORD = Path(".ci/performance/shared-laplacian-storage-latest.json")
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
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
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
            "graph-build",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "graph": release / "graph-build",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-shared-graph-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_line = next(
        line
        for line in time_path.read_text().splitlines()
        if "Maximum resident set size (kbytes):" in line
    )
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_line.rsplit(":", 1)[1].strip())
    return payload


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
        )
    ):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = ("case", "scale", "vertices", "repetitions")
    if kind == "graph":
        stable += ("raw_edges", "retained_edges")
    else:
        stable += ("edges",)
    if kind == "allocation":
        stable += (
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {kind} metadata for {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": statistics.median(
            item["median_ns"] for item in baseline_samples
        ),
        "candidate_median_ns": statistics.median(
            item["median_ns"] for item in candidate_samples
        ),
        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    if kind == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_value = statistics.median(
                item[field] for item in baseline_samples
            )
            candidate_value = statistics.median(
                item[field] for item in candidate_samples
            )
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = (
                candidate_value / baseline_value
            )
    return result


OLD_FIELDS = '''    edges: Vec<Edge>,
    diagonal: Vec<f64>,
'''
NEW_FIELDS = '''    edges: Arc<Vec<Edge>>,
    diagonal: Arc<Vec<f64>>,
'''
OLD_INITIALIZATION = '''            edges: raw,
            diagonal,
'''
NEW_INITIALIZATION = '''            edges: Arc::new(raw),
            diagonal: Arc::new(diagonal),
'''
OLD_EDGES_ACCESSOR = '''    pub fn edges(&self) -> &[Edge] {
        &self.edges
    }
'''
NEW_EDGES_ACCESSOR = '''    pub fn edges(&self) -> &[Edge] {
        self.edges.as_slice()
    }
'''
OLD_DIAGONAL_ACCESSOR = '''    pub fn diagonal(&self) -> &[f64] {
        &self.diagonal
    }
'''
NEW_DIAGONAL_ACCESSOR = '''    pub fn diagonal(&self) -> &[f64] {
        self.diagonal.as_slice()
    }
'''
OLD_EDGE_LOOP = "        for edge in &self.edges {\n"
NEW_EDGE_LOOP = "        for edge in self.edges.iter() {\n"
TEST_MODULE = '''

#[cfg(test)]
mod shared_laplacian_storage_tests {
    use super::Laplacian;
    use std::sync::Arc;

    #[test]
    fn clones_share_immutable_edge_and_diagonal_storage() {
        let graph = Laplacian::from_edges(
            5,
            [(0, 1, 1.0), (1, 2, 2.0), (2, 3, 3.0), (3, 4, 4.0)],
        )
        .unwrap();
        let clone = graph.clone();
        assert!(Arc::ptr_eq(&graph.edges, &clone.edges));
        assert!(Arc::ptr_eq(&graph.diagonal, &clone.diagonal));
        assert!(graph.shares_lineage(&clone));
        assert_eq!(graph, clone);
    }

    #[test]
    fn independently_built_equal_graphs_do_not_share_storage() {
        let left = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();
        let right = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();
        assert_eq!(left, right);
        assert!(!Arc::ptr_eq(&left.edges, &right.edges));
        assert!(!Arc::ptr_eq(&left.diagonal, &right.diagonal));
    }
}
'''


def apply_candidate(source):
    candidate = source
    for old, new, name in (
        (OLD_FIELDS, NEW_FIELDS, "Laplacian buffers"),
        (OLD_INITIALIZATION, NEW_INITIALIZATION, "Laplacian initialization"),
        (OLD_EDGES_ACCESSOR, NEW_EDGES_ACCESSOR, "edge accessor"),
        (OLD_DIAGONAL_ACCESSOR, NEW_DIAGONAL_ACCESSOR, "diagonal accessor"),
    ):
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_EDGE_LOOP) != 2:
        raise RuntimeError(
            f"expected two direct edge loops, found {candidate.count(OLD_EDGE_LOOP)}"
        )
    candidate = candidate.replace(OLD_EDGE_LOOP, NEW_EDGE_LOOP)
    if "mod shared_laplacian_storage_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    graph_ratio = result.get("graph_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    peak_ratio = result.get("geometric_additional_peak_ratio", 1.0)
    retained_ratio = result.get("geometric_retained_ratio", 1.0)
    checkpoint = f'''### Shared Laplacian storage checkpoint — 2026-08-24

- Sharing immutable edge and diagonal buffers across `Laplacian` clones was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/shared-laplacian-storage-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Shared Laplacian storage checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Shared Laplacian storage gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Graph-build / hierarchy-build ratios: `{graph_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/shared-laplacian-storage-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Shared Laplacian storage gate\n"
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
    "experiment": "shared-laplacian-storage",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "graph_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-shared-graph-baseline"))
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
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-shared-graph-candidate"))
    graph_specs = (
        ("unique-1m", ["unique", "1000000", "3"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "3"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "3"]),
    )
    for name, arguments in graph_specs:
        result["graph_cases"][name] = compare(
            "graph", baseline, candidate, arguments, name
        )
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    graph_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["graph_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    peak_ratios = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (
            result["graph_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
    result["graph_geometric_time_ratio"] = geometric(graph_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["worst_graph_time_ratio"] = max(graph_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["acceptance_limits"] = {
        "graph_geometric_time_ratio_max": 1.015,
        "worst_graph_time_ratio_max": 1.04,
        "hierarchy_geometric_time_ratio_max": 0.99,
        "worst_hierarchy_time_ratio_max": 1.02,
        "geometric_additional_peak_ratio_max": 0.90,
        "worst_additional_peak_ratio_max": 0.95,
        "geometric_retained_ratio_max": 0.90,
        "worst_retained_ratio_max": 0.95,
        "worst_peak_rss_ratio_max": 1.02,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["graph_geometric_time_ratio"] <= 1.015
        and result["worst_graph_time_ratio"] <= 1.04
        and result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["worst_hierarchy_time_ratio"] <= 1.02
        and result["geometric_additional_peak_ratio"] <= 0.90
        and result["worst_additional_peak_ratio"] <= 0.95
        and result["geometric_retained_ratio"] <= 0.90
        and result["worst_retained_ratio"] <= 0.95
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; immutable fine-graph buffers are shared across clones with materially lower hierarchy peak and retained allocation"
        if result["accepted"]
        else "validation passed, but construction timing or exact/process memory gates were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "graph_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
    "worst_graph_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
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
    "perf: retain shared Laplacian storage"
    if result.get("accepted", False)
    else "perf: record shared Laplacian storage experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push shared Laplacian storage decision")
