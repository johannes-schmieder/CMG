from pathlib import Path
import subprocess

OUTER_COMMIT = "29f510fbdbc35d91f7362da58f53de20a10428e2"
OUTER_PATH = "scripts/routed_endpoint_axis_sort_gate.py"

outer = subprocess.check_output(
    ["git", "show", f"{OUTER_COMMIT}:{OUTER_PATH}"],
    text=True,
)
execution_marker = 'compile(text, str(Path(__file__)), "exec")'
if execution_marker not in outer:
    raise SystemExit("historical routed wrapper execution marker missing")
namespace = {
    "__name__": "routed_endpoint_axis_wrapper_defs",
    "__file__": __file__,
}
exec(
    compile(outer.split(execution_marker, 1)[0], str(Path(__file__)), "exec"),
    namespace,
)
gate = namespace["text"]

replacements = (
    ("routed_endpoint_axis_sort_gate.py", "dense_endpoint_axis_sort_gate_v2.py"),
    ("routed-endpoint-axis-sort.yml", "dense-endpoint-axis-sort-v2.yml"),
    ("routed-endpoint-axis-sort-latest.json", "dense-endpoint-axis-sort-latest.json"),
    ("routed-endpoint-axis-compact-sort", "dense-endpoint-axis-compact-sort"),
    ("Routed endpoint-axis compact sort", "Dense endpoint-axis compact sort"),
    ("routed endpoint-axis compact sort", "dense endpoint-axis compact sort"),
    ("routed_hierarchy_geometric_time_ratio", "active_hierarchy_geometric_time_ratio"),
    ("improved_routed_hierarchy_count", "improved_active_hierarchy_count"),
)
for old, new in replacements:
    gate = gate.replace(old, new)

# Fix the pre-existing direct unit-test invocation for the routed helper.
old_test_call = "        sort_compact_edge_endpoints(&mut compact);\n"
if gate.count(old_test_call) != 1:
    raise SystemExit("direct compact-path unit-test call changed unexpectedly")
gate = gate.replace(
    old_test_call,
    "        sort_compact_edge_endpoints(6, &mut compact);\n",
    1,
)

old_router = """const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
const ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES: usize = 500_000;
const ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_endpoint_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= ENDPOINT_AXIS_SORT_MIN_EDGES
        || (edge_count >= ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES
            && edge_count
                >= vertex_count.saturating_mul(ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX))
}
"""
new_router = """const ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES: usize = 500_000;
const ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_endpoint_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES
        && edge_count
            >= vertex_count.saturating_mul(ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX)
}
"""
if gate.count(old_router) != 1:
    raise SystemExit("generated endpoint-axis router changed unexpectedly")
gate = gate.replace(old_router, new_router, 1)
gate = gate.replace(
    "assert!(should_use_endpoint_axis_sort(1_500_000, 2_250_000));",
    "assert!(!should_use_endpoint_axis_sort(1_500_000, 2_250_000));",
    1,
)

old_specs = """    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-2.25m", "worker-firm", "750000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-800k", "dense-worker-firm", "50000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
"""
new_specs = """    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-2.25m", "worker-firm", "750000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-400k", "dense-worker-firm", "25000"),
        ("dense-worker-firm-600k", "dense-worker-firm", "37500"),
        ("dense-worker-firm-800k", "dense-worker-firm", "50000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
"""
if gate.count(old_specs) != 1:
    raise SystemExit("generated routed benchmark matrix changed unexpectedly")
gate = gate.replace(old_specs, new_specs, 1)

calc_start = gate.index("    contraction_ratios = [")
calc_end = gate.index("except Exception as error:", calc_start)
calculation = r'''    contraction_ratios = [
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
        "active_hierarchy_geometric_time_ratio_max": 0.985,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_control_contraction_time_ratio_max": 1.02,
        "worst_control_hierarchy_time_ratio_max": 1.02,
        "worst_contraction_time_ratio_max": 1.03,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_active_contraction_count_min": 3,
        "improved_active_hierarchy_count_min": 3,
    }
    result["accepted"] = (
        result["active_contraction_geometric_time_ratio"] <= 0.985
        and result["active_hierarchy_geometric_time_ratio"] <= 0.985
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_control_contraction_time_ratio"] <= 1.02
        and result["worst_control_hierarchy_time_ratio"] <= 1.02
        and result["worst_contraction_time_ratio"] <= 1.03
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_active_contraction_count"] >= 3
        and result["improved_active_hierarchy_count"] >= 3
    )
    result["decision_reason"] = (
        "full qualification passed; endpoint-axis sorting is retained only for large dense levels with low duplicate rates, while path and worker-firm levels keep packed-key sorting"
        if result["accepted"]
        else "correctness passed, but dense active, control, hierarchy, or memory gates were not all met"
    )
'''
gate = gate[:calc_start] + calculation + gate[calc_end:]

gate = gate.replace(
    "Measured work/density routing between packed-key sorting and endpoint-axis sorting",
    "Density-only routing between packed-key sorting and endpoint-axis sorting",
)
gate = gate.replace(
    "the measured work/density router retains axis sorting only where it improves contraction and complete hierarchy construction",
    "the density-only router retains axis sorting only for large dense levels and leaves all path and worker-firm controls on packed-key sorting",
)
gate = gate.replace(
    "perf: retain routed endpoint-axis compact sorting",
    "perf: retain dense endpoint-axis compact sorting",
)
gate = gate.replace(
    "perf: record routed endpoint-axis sort experiment",
    "perf: record dense endpoint-axis sort experiment",
)

required = (
    "dense_endpoint_axis_sort_gate_v2.py",
    "dense-endpoint-axis-sort-v2.yml",
    "dense-endpoint-axis-sort-latest.json",
    "ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES",
    '"dense-worker-firm-600k"',
    '"active_hierarchy_geometric_time_ratio_max": 0.985',
)
for marker in required:
    if marker not in gate:
        raise SystemExit(f"direct dense endpoint gate missing marker: {marker}")

compile(gate, str(Path(__file__)), "exec")
exec(compile(gate, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
