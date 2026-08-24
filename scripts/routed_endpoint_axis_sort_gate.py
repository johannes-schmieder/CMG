from pathlib import Path
import subprocess

SOURCE_COMMIT = "a8a05dc3c0091d99da42132e3c36cdec5beae6e2"
SOURCE_PATH = "scripts/endpoint_axis_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

replacements = (
    ("endpoint_axis_sort_gate.py", "routed_endpoint_axis_sort_gate.py"),
    ("endpoint-axis-sort.yml", "routed-endpoint-axis-sort.yml"),
    ("endpoint-axis-sort-latest.json", "routed-endpoint-axis-sort-latest.json"),
    ("endpoint-axis-compact-sort", "routed-endpoint-axis-compact-sort"),
    ("Endpoint-axis compact sort", "Routed endpoint-axis compact sort"),
    ("endpoint-axis compact sort", "routed endpoint-axis compact sort"),
)
for old, new in replacements:
    text = text.replace(old, new)

constants_start = text.index("OLD_SORT =")
constants_end = text.index("\n\ndef apply_candidate", constants_start)
constants = r"""OLD_SORT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
OLD_CONSTRUCTOR_CALL = '''        sort_compact_edge_endpoints(&mut raw);
'''
OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
const ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES: usize = 500_000;
const ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_endpoint_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= ENDPOINT_AXIS_SORT_MIN_EDGES
        || (edge_count >= ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES
            && edge_count
                >= vertex_count.saturating_mul(ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX))
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
mod routed_endpoint_axis_sort_tests {
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
    fn router_matches_qualified_work_and_density_thresholds() {
        assert!(!should_use_endpoint_axis_sort(1_000_000, 1_500_000));
        assert!(should_use_endpoint_axis_sort(1_500_000, 2_250_000));
        assert!(should_use_endpoint_axis_sort(100_000, 800_000));
        assert!(!should_use_endpoint_axis_sort(1_000_000, 999_999));
        assert!(!should_use_endpoint_axis_sort(100_000, 399_999));
    }
}
'''
"""
text = text[:constants_start] + constants + text[constants_end:]

apply_start = text.index("def apply_candidate(source):")
apply_end = text.index("\n\ndef geometric", apply_start)
apply_function = r'''def apply_candidate(source):
    if source.count(OLD_SORT) != 1:
        raise RuntimeError("compact endpoint sorter marker changed unexpectedly")
    if source.count(OLD_CONSTRUCTOR_CALL) != 2:
        raise RuntimeError("compact endpoint constructor calls changed unexpectedly")
    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test-only compact sorter call changed unexpectedly")
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
    if "mod routed_endpoint_axis_sort_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:apply_start] + apply_function + text[apply_end:]

old_specs = '''    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
'''
new_specs = '''    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-2.25m", "worker-firm", "750000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-800k", "dense-worker-firm", "50000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
'''
if text.count(old_specs) != 1:
    raise SystemExit("historical endpoint-axis benchmark matrix changed unexpectedly")
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
    routed_names = (
        "worker-firm-2.25m",
        "worker-firm-3m",
        "dense-worker-firm-800k",
        "dense-worker-firm-1.6m",
    )
    control_names = ("path-1m", "worker-firm-1.5m")
    routed_contraction = [
        result["contraction_cases"][name]["candidate_over_baseline_time"]
        for name in routed_names
    ]
    routed_hierarchy = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in routed_names
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
    result["active_contraction_geometric_time_ratio"] = geometric(routed_contraction)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["routed_hierarchy_geometric_time_ratio"] = geometric(routed_hierarchy)
    result["worst_control_contraction_time_ratio"] = max(control_contraction)
    result["worst_control_hierarchy_time_ratio"] = max(control_hierarchy)
    result["worst_contraction_time_ratio"] = max(contraction_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_active_contraction_count"] = sum(
        value < 1.0 for value in routed_contraction
    )
    result["improved_routed_hierarchy_count"] = sum(
        value < 1.0 for value in routed_hierarchy
    )
    result["acceptance_limits"] = {
        "active_contraction_geometric_time_ratio_max": 0.985,
        "routed_hierarchy_geometric_time_ratio_max": 0.98,
        "hierarchy_geometric_time_ratio_max": 0.99,
        "worst_control_contraction_time_ratio_max": 1.02,
        "worst_control_hierarchy_time_ratio_max": 1.02,
        "worst_contraction_time_ratio_max": 1.03,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_active_contraction_count_min": 3,
        "improved_routed_hierarchy_count_min": 4,
    }
    result["accepted"] = (
        result["active_contraction_geometric_time_ratio"] <= 0.985
        and result["routed_hierarchy_geometric_time_ratio"] <= 0.98
        and result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["worst_control_contraction_time_ratio"] <= 1.02
        and result["worst_control_hierarchy_time_ratio"] <= 1.02
        and result["worst_contraction_time_ratio"] <= 1.03
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_active_contraction_count"] >= 3
        and result["improved_routed_hierarchy_count"] >= 4
    )
    result["decision_reason"] = (
        "full qualification passed; the measured work/density router retains axis sorting only where it improves contraction and complete hierarchy construction"
        if result["accepted"]
        else "correctness passed, but routed contraction, hierarchy, control, or memory gates were not all met"
    )
'''
text = text[:calc_start] + calculation + text[calc_end:]

text = text.replace(
    "Global first-endpoint sorting followed by second-endpoint bucket sorting",
    "Measured work/density routing between packed-key sorting and endpoint-axis sorting",
)
text = text.replace(
    "axis-local endpoint sorting materially improved worker-firm contraction and complete hierarchy construction without extra storage",
    "the endpoint-axis route improved only qualified large or dense levels while controls retained packed-key sorting",
)
text = text.replace(
    "perf: retain endpoint-axis compact sorting",
    "perf: retain routed endpoint-axis compact sorting",
)
text = text.replace(
    "perf: record endpoint-axis sort experiment",
    "perf: record routed endpoint-axis sort experiment",
)

required = (
    "routed_endpoint_axis_sort_gate.py",
    "routed-endpoint-axis-sort.yml",
    "routed-endpoint-axis-sort-latest.json",
    "should_use_endpoint_axis_sort",
    '"worker-firm-2.25m"',
    '"routed_hierarchy_geometric_time_ratio_max": 0.98',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"routed endpoint-axis gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
