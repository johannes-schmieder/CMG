from pathlib import Path
import subprocess

SOURCE_COMMIT = "926d352c60a6918ab32e2d4cfedcab75fcb57643"
SOURCE_PATH = "scripts/internal_serial_heavy_edge_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "internal_serial_heavy_edge_gate.py",
    "dense_serial_heavy_edge_gate.py",
)
text = text.replace(
    "internal-serial-heavy-edge.yml",
    "dense-serial-heavy-edge.yml",
)
text = text.replace(
    "internal-serial-heavy-edge-latest.json",
    "dense-serial-heavy-edge-latest.json",
)
text = text.replace(
    "internal-serial-heavy-edge-routing",
    "dense-serial-heavy-edge-routing",
)
if text.count("options.clone()") != 3:
    raise SystemExit("expected three benchmark-only CmgOptions clones")
text = text.replace("options.clone()", "options")

start = text.index("OLD_INTERNAL =")
end = text.index("\n\ndef apply_candidate", start)
source_candidate = r'''OLD_INTERNAL = '''pub(crate) fn build_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    executor: &ParallelExecutor,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest_with_executor(graph, executor)?;
'''
NEW_INTERNAL = '''pub(crate) fn build_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    executor: &ParallelExecutor,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let directed_entries = graph.edge_count().saturating_mul(2);
    let density_floor = graph.vertex_count().saturating_mul(3);
    let one_use_csr_would_be_built = directed_entries >= PARALLEL_SETUP_MIN_ITEMS
        && directed_entries > density_floor
        && executor.should_parallel(directed_entries);
    let (heavy_parent, selected_weight) = if one_use_csr_would_be_built {
        // Hierarchy setup parallelizes coarse contraction and sorting separately.
        // Avoid materializing a temporary duplicated CSR solely for one heavy-edge
        // selection pass; the public executor API remains available to callers.
        maximum_weight_forest(graph)
    } else {
        maximum_weight_forest_with_executor(graph, executor)?
    };
'''
TEST_MODULE = '''

#[cfg(all(test, feature = "parallel"))]
mod dense_serial_heavy_edge_tests {
    use super::{build_forest_aggregation_labels_with_executor, build_forest_aggregation_labels};
    use crate::{Laplacian, ParallelExecutor, ParallelOptions};

    #[test]
    fn dense_executor_hierarchy_grouping_matches_serial_grouping() {
        let graph = Laplacian::from_edges(
            4_000,
            (0..2_000).flat_map(|worker| {
                (0..16).map(move |link| {
                    let firm = ((2 * link + 1) * worker + 17 * link + 3) % 2_000;
                    (worker, 2_000 + firm, 0.25 + link as f64 / 16.0)
                })
            }),
        )
        .unwrap();
        let executor = ParallelExecutor::new(ParallelOptions {
            threads: 4,
            min_parallel_len: 1,
            ..ParallelOptions::default()
        })
        .unwrap();
        let serial = build_forest_aggregation_labels(&graph, 0.125).unwrap();
        let routed = build_forest_aggregation_labels_with_executor(
            &graph,
            0.125,
            &executor,
        )
        .unwrap();
        assert_eq!(routed, serial);
    }
}
'''
'''
text = text[:start] + source_candidate + text[end:]

# Replace the old decision metrics with dense-primary routing gates.
old_metric_block = '''    ratios = [
        case["candidate_over_baseline_time"]
        for case in result["cases"].values()
    ]
    active_names = (
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-1.6m",
        "dense-worker-firm-3.2m",
    )
    active_ratios = [
        result["cases"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    rss_ratios = [
        case["candidate_over_baseline_median_rss"]
        for case in result["cases"].values()
    ]
    result["active_geometric_time_ratio"] = geometric(active_ratios)
    result["all_geometric_time_ratio"] = geometric(ratios)
    result["worst_time_ratio"] = max(ratios)
    result["worst_median_rss_ratio"] = max(rss_ratios)
    result["improved_active_case_count"] = sum(value < 1.0 for value in active_ratios)
    result["acceptance_limits"] = {
        "active_geometric_time_ratio_max": 0.97,
        "all_geometric_time_ratio_max": 0.985,
        "worst_time_ratio_max": 1.03,
        "worst_median_rss_ratio_max": 1.02,
        "improved_active_case_count_min": 3,
    }
    result["accepted"] = (
        result["active_geometric_time_ratio"] <= 0.97
        and result["all_geometric_time_ratio"] <= 0.985
        and result["worst_time_ratio"] <= 1.03
        and result["worst_median_rss_ratio"] <= 1.02
        and result["improved_active_case_count"] >= 3
    )
'''
new_metric_block = '''    ratios = [
        case["candidate_over_baseline_time"]
        for case in result["cases"].values()
    ]
    dense_names = (
        "dense-worker-firm-1.6m",
        "dense-worker-firm-3.2m",
    )
    control_names = (
        "path-1m",
        "worker-firm-1.5m",
        "worker-firm-3m",
    )
    dense_time = [
        result["cases"][name]["candidate_over_baseline_time"]
        for name in dense_names
    ]
    dense_rss = [
        result["cases"][name]["candidate_over_baseline_median_rss"]
        for name in dense_names
    ]
    control_time = [
        result["cases"][name]["candidate_over_baseline_time"]
        for name in control_names
    ]
    control_rss = [
        result["cases"][name]["candidate_over_baseline_median_rss"]
        for name in control_names
    ]
    result["active_geometric_time_ratio"] = geometric(dense_time)
    result["all_geometric_time_ratio"] = geometric(ratios)
    result["dense_geometric_median_rss_ratio"] = geometric(dense_rss)
    result["worst_time_ratio"] = max(ratios)
    result["worst_median_rss_ratio"] = max(dense_rss + control_rss)
    result["worst_control_time_ratio"] = max(control_time)
    result["worst_control_median_rss_ratio"] = max(control_rss)
    result["improved_active_case_count"] = sum(value < 1.0 for value in dense_time)
    result["acceptance_limits"] = {
        "dense_geometric_time_ratio_max": 0.85,
        "all_geometric_time_ratio_max": 0.96,
        "dense_geometric_median_rss_ratio_max": 0.93,
        "worst_control_time_ratio_max": 1.05,
        "worst_control_median_rss_ratio_max": 1.08,
        "improved_dense_case_count_min": 2,
    }
    result["accepted"] = (
        result["active_geometric_time_ratio"] <= 0.85
        and result["all_geometric_time_ratio"] <= 0.96
        and result["dense_geometric_median_rss_ratio"] <= 0.93
        and result["worst_control_time_ratio"] <= 1.05
        and result["worst_control_median_rss_ratio"] <= 1.08
        and result["improved_active_case_count"] == 2
    )
'''
if text.count(old_metric_block) != 1:
    raise SystemExit("historical heavy-edge decision block changed unexpectedly")
text = text.replace(old_metric_block, new_metric_block, 1)

text = text.replace(
    "Internal serial heavy-edge routing checkpoint",
    "Dense serial heavy-edge routing checkpoint",
)
text = text.replace(
    "Using the compact serial edge scan for hierarchy-internal heavy-edge selection",
    "Avoiding one-use CSR construction for dense hierarchy heavy-edge selection",
)
text = text.replace(
    "Active worker/dense geometric hierarchy-build ratio",
    "Dense geometric hierarchy-build ratio",
)
text = text.replace(
    "Internal serial heavy-edge routing gate",
    "Dense serial heavy-edge routing gate",
)
text = text.replace(
    "Worker/dense hierarchy-build ratio",
    "Dense hierarchy-build ratio",
)
text = text.replace(
    "full qualification passed; avoiding a one-use CSR build improved executor hierarchy setup while preserving the exact hierarchy",
    "dense routing passed; one-use CSR construction was removed only where it materially slowed hierarchy setup and increased memory",
)
text = text.replace(
    "correctness passed, but executor hierarchy timing or RSS limits were not all met",
    "correctness passed, but dense speed/memory or unchanged-path control limits were not all met",
)
text = text.replace(
    "perf: retain serial heavy-edge selection for hierarchy setup",
    "perf: retain dense serial heavy-edge routing",
)
text = text.replace(
    "perf: record internal heavy-edge routing experiment",
    "perf: record dense heavy-edge routing experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/internal-heavy-edge-run-status.json",
    ".ci/performance/internal-serial-heavy-edge-latest.json",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("dense heavy-edge cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "dense_serial_heavy_edge_gate.py",
    "dense-serial-heavy-edge.yml",
    "one_use_csr_would_be_built",
    "dense_geometric_median_rss_ratio",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"density-routed heavy-edge gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
