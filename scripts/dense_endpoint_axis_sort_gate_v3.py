from pathlib import Path
import subprocess

BASE_COMMIT = "a8a05dc3c0091d99da42132e3c36cdec5beae6e2"
BASE_PATH = "scripts/endpoint_axis_sort_gate.py"

gate = subprocess.check_output(
    ["git", "show", f"{BASE_COMMIT}:{BASE_PATH}"],
    text=True,
)

replacements = (
    ("endpoint_axis_sort_gate.py", "dense_endpoint_axis_sort_gate_v3.py"),
    ("endpoint-axis-sort.yml", "dense-endpoint-axis-sort-v3.yml"),
    ("endpoint-axis-sort-latest.json", "dense-endpoint-axis-sort-latest.json"),
    ("endpoint-axis-compact-sort", "dense-endpoint-axis-compact-sort"),
    ("Endpoint-axis compact sort", "Dense endpoint-axis compact sort"),
    ("endpoint-axis compact sort", "dense endpoint-axis compact sort"),
)
for old, new in replacements:
    gate = gate.replace(old, new)

constants_start = gate.index("OLD_SORT =")
constants_end = gate.index("\n\ndef apply_candidate", constants_start)
constants = r"""OLD_SORT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
OLD_CONSTRUCTOR_CALL = '''        sort_compact_edge_endpoints(&mut raw);
'''
OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
OLD_DIRECT_TEST_CALL = '''        sort_compact_edge_endpoints(&mut compact);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES: usize = 500_000;
const ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_endpoint_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES
        && edge_count
            >= vertex_count.saturating_mul(ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX)
}

fn sort_compact_edge_endpoints(vertex_count: usize, raw: &mut [Edge]) {
    if should_use_endpoint_axis_sort(vertex_count, raw.len()) {
        sort_compact_endpoint_axes(raw);
    } else {
        sort_packed_endpoint_keys(raw);
    }
}

fn sort_packed_endpoint_keys(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

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
TEST_MODULE = '''

#[cfg(test)]
mod dense_endpoint_axis_sort_tests {
    use super::{
        Edge, endpoint_key, should_use_endpoint_axis_sort,
        sort_compact_endpoint_axes,
    };

    fn generated_edges() -> Vec<Edge> {
        let mut edges = Vec::new();
        for index in 0..16_384_usize {
            let left = (37 * index + 11) % 4_003;
            let mut right = (97 * index + 29) % 4_003;
            if right == left {
                right = (right + 1) % 4_003;
            }
            for duplicate in 0..3_usize {
                let weight = 0.25 + ((index + 19 * duplicate) % 127) as f64 / 64.0;
                edges.push(Edge::from_internal_parts(left, right, weight).unwrap());
            }
        }
        edges.reverse();
        edges
    }

    #[test]
    fn endpoint_axis_sort_matches_packed_endpoint_order() {
        let mut candidate = generated_edges();
        let mut reference = candidate.clone();
        sort_compact_endpoint_axes(&mut candidate);
        reference.sort_unstable_by_key(endpoint_key);
        let candidate_keys: Vec<_> = candidate.iter().map(endpoint_key).collect();
        let reference_keys: Vec<_> = reference.iter().map(endpoint_key).collect();
        assert_eq!(candidate_keys, reference_keys);
    }

    #[test]
    fn router_selects_only_large_dense_levels() {
        assert!(!should_use_endpoint_axis_sort(1_000_000, 1_500_000));
        assert!(!should_use_endpoint_axis_sort(1_500_000, 2_250_000));
        assert!(!should_use_endpoint_axis_sort(50_000, 400_000));
        assert!(should_use_endpoint_axis_sort(75_000, 600_000));
        assert!(should_use_endpoint_axis_sort(100_000, 800_000));
    }
}
'''
"""
gate = gate[:constants_start] + constants + gate[constants_end:]

apply_start = gate.index("def apply_candidate(source):")
apply_end = gate.index("\n\ndef geometric", apply_start)
apply_function = r'''def apply_candidate(source):
    markers = (
        (OLD_SORT, 1, "compact endpoint sorter"),
        (OLD_CONSTRUCTOR_CALL, 2, "compact constructor calls"),
        (OLD_TEST_HELPER_CALL, 1, "test helper call"),
        (OLD_DIRECT_TEST_CALL, 1, "direct compact-path test call"),
    )
    for marker, expected, name in markers:
        if source.count(marker) != expected:
            raise RuntimeError(
                f"{name} changed unexpectedly: expected {expected}, "
                f"found {source.count(marker)}"
            )
    candidate = source.replace(OLD_SORT, NEW_SORT, 1)
    candidate = candidate.replace(
        OLD_CONSTRUCTOR_CALL,
        "        sort_compact_edge_endpoints(vertex_count, &mut raw);\n",
    )
    candidate = candidate.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\n",
        1,
    )
    candidate = candidate.replace(
        OLD_DIRECT_TEST_CALL,
        "        sort_compact_edge_endpoints(6, &mut compact);\n",
        1,
    )
    if "mod dense_endpoint_axis_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
gate = gate[:apply_start] + apply_function + gate[apply_end:]

old_specs = """    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
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
    raise SystemExit("base benchmark matrix changed unexpectedly")
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
    "Global first-endpoint sorting followed by second-endpoint bucket sorting",
    "Density-only routing between packed-key sorting and endpoint-axis sorting",
)
gate = gate.replace(
    "axis-local endpoint sorting materially improved worker-firm contraction and complete hierarchy construction without extra storage",
    "the density-only route improved large dense levels while path and worker-firm controls retained packed-key sorting",
)
gate = gate.replace(
    "perf: retain endpoint-axis compact sorting",
    "perf: retain dense endpoint-axis compact sorting",
)
gate = gate.replace(
    "perf: record endpoint-axis sort experiment",
    "perf: record dense endpoint-axis sort experiment",
)

required = (
    "dense_endpoint_axis_sort_gate_v3.py",
    "dense-endpoint-axis-sort-v3.yml",
    "dense-endpoint-axis-sort-latest.json",
    "ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES",
    '"dense-worker-firm-600k"',
    '"active_hierarchy_geometric_time_ratio_max": 0.985',
)
for marker in required:
    if marker not in gate:
        raise SystemExit(f"direct dense endpoint v3 gate missing marker: {marker}")

compile(gate, str(Path(__file__)), "exec")
exec(compile(gate, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
