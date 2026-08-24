from pathlib import Path
import subprocess

SOURCE_COMMIT = "29f510fbdbc35d91f7362da58f53de20a10428e2"
SOURCE_PATH = "scripts/routed_endpoint_axis_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "routed_endpoint_axis_sort_gate.py",
    "dense_endpoint_axis_sort_gate.py",
)
text = text.replace(
    "routed-endpoint-axis-sort.yml",
    "dense-endpoint-axis-sort.yml",
)
text = text.replace(
    "routed-endpoint-axis-sort-latest.json",
    "dense-endpoint-axis-sort-latest.json",
)
text = text.replace(
    "routed-endpoint-axis-compact-sort",
    "dense-endpoint-axis-compact-sort",
)
text = text.replace(
    "Routed endpoint-axis compact sort",
    "Dense endpoint-axis compact sort",
)
text = text.replace(
    "routed endpoint-axis compact sort",
    "dense endpoint-axis compact sort",
)

# Repair the one pre-existing unit-test call for the routed helper signature.
old_constant = """OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
"""
new_constant = """OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
OLD_DIRECT_TEST_CALL = '''        sort_compact_edge_endpoints(&mut compact);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
"""
if text.count(old_constant) != 1:
    raise SystemExit("historical routed constant marker changed unexpectedly")
text = text.replace(old_constant, new_constant, 1)

old_apply_checks = """    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test-only compact sorter call changed unexpectedly")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
"""
new_apply_checks = """    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test-only compact sorter call changed unexpectedly")
    if source.count(OLD_DIRECT_TEST_CALL) != 1:
        raise RuntimeError("direct compact-path test call changed unexpectedly")
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
"""
if text.count(old_apply_checks) != 1:
    raise SystemExit("historical routed apply checks changed unexpectedly")
text = text.replace(old_apply_checks, new_apply_checks, 1)

old_apply_tail = """    candidate = candidate.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\\n",
        1,
    )
    if "mod routed_endpoint_axis_sort_tests" not in candidate:
"""
new_apply_tail = """    candidate = candidate.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\\n",
        1,
    )
    candidate = candidate.replace(
        OLD_DIRECT_TEST_CALL,
        "        sort_compact_edge_endpoints(6, &mut compact);\\n",
        1,
    )
    if "mod routed_endpoint_axis_sort_tests" not in candidate:
"""
if text.count(old_apply_tail) != 1:
    raise SystemExit("historical routed apply tail changed unexpectedly")
text = text.replace(old_apply_tail, new_apply_tail, 1)

# Remove the broad size-only route. Axis sorting is now selected only for
# sufficiently large, high-edge-density levels.
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
if text.count(old_router) != 1:
    raise SystemExit("historical endpoint-axis router changed unexpectedly")
text = text.replace(old_router, new_router, 1)
text = text.replace(
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
if text.count(old_specs) != 1:
    raise SystemExit("historical routed benchmark matrix changed unexpectedly")
text = text.replace(old_specs, new_specs, 1)

calc_start = text.index("    contraction_ratios = [")
calc_end = text.index("except Exception as error:", calc_start)
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
text = text[:calc_start] + calculation + text[calc_end:]

text = text.replace(
    "Measured work/density routing between packed-key sorting and endpoint-axis sorting",
    "Density-only routing between packed-key sorting and endpoint-axis sorting",
)
text = text.replace(
    "the measured work/density router retains axis sorting only where it improves contraction and complete hierarchy construction",
    "the density-only router retains axis sorting only for large dense levels and leaves all worker-firm controls unchanged",
)
text = text.replace(
    "perf: retain routed endpoint-axis compact sorting",
    "perf: retain dense endpoint-axis compact sorting",
)
text = text.replace(
    "perf: record routed endpoint-axis sort experiment",
    "perf: record dense endpoint-axis sort experiment",
)

required = (
    "dense_endpoint_axis_sort_gate.py",
    "dense-endpoint-axis-sort.yml",
    "dense-endpoint-axis-sort-latest.json",
    "ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES",
    '"dense-worker-firm-600k"',
    '"active_hierarchy_geometric_time_ratio_max": 0.985',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"dense endpoint-axis gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
