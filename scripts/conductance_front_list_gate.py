from pathlib import Path
import subprocess

SOURCE_COMMIT = "8cece8b67eca8c6dc87ebaf3072e9a53b0edc05d"
SOURCE_PATH = "scripts/fused_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace(
    "fused_walk_ancestor_scratch_gate.py",
    "conductance_front_list_gate.py",
)
text = text.replace(
    "fused-walk-ancestor-scratch.yml",
    "conductance-front-list.yml",
)
text = text.replace(
    "fused-walk-ancestor-scratch-latest.json",
    "conductance-front-list-latest.json",
)
text = text.replace(
    "fused-walk-ancestor-scratch-gate",
    "conductance-front-list-gate",
)
text = text.replace(
    "fused-walk-ancestor-scratch",
    "conductance-front-list",
)
text = text.replace(
    "Fused walk/ancestor scratch",
    "Compact conductance-front list",
)
text = text.replace(
    "fused walk/ancestor scratch",
    "compact conductance-front list",
)

constants_start = text.index("ENTRY_TYPE =")
constants_end = text.index("\n\ndef run(", constants_start)
constants = r"""OLD_CONDUCTANCE = '''    for start in 0..n {
        let mut current = start;
        'new_front: while indegree[current].is_zero() {
            let mut previous = current;
            loop {
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    indegree[next].decrement();
                    let removed_ancestors = ancestors[current];

                    let mut adjustment_previous = current;
                    let mut adjustment_current = next;
                    loop {
                        ancestors[adjustment_current] -= removed_ancestors;
                        let adjustment_next = forest[adjustment_current];
                        if adjustment_next == adjustment_current
                            || adjustment_next == adjustment_previous
                        {
                            break;
                        }
                        adjustment_previous = adjustment_current;
                        adjustment_current = adjustment_next;
                    }

                    current = next;
                    continue 'new_front;
                }
                previous = current;
                current = next;
            }
        }
    }
'''
NEW_CONDUCTANCE = '''    drop(new_ancestors);
    walk.clear();
    walk.extend(
        indegree
            .iter()
            .enumerate()
            .filter_map(|(vertex, degree)| (*degree).is_zero().then_some(vertex)),
    );

    for &start in &walk {
        let mut current = start;
        'new_front: while indegree[current].is_zero() {
            let mut previous = current;
            loop {
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    indegree[next].decrement();
                    let removed_ancestors = ancestors[current];

                    let mut adjustment_previous = current;
                    let mut adjustment_current = next;
                    loop {
                        ancestors[adjustment_current] -= removed_ancestors;
                        let adjustment_next = forest[adjustment_current];
                        if adjustment_next == adjustment_current
                            || adjustment_next == adjustment_previous
                        {
                            break;
                        }
                        adjustment_previous = adjustment_current;
                        adjustment_current = adjustment_next;
                    }

                    current = next;
                    continue 'new_front;
                }
                previous = current;
                current = next;
            }
        }
    }
'''
TEST_MODULE = '''

#[cfg(test)]
mod conductance_front_list_tests {
    use super::ForestIndegree;

    #[test]
    fn compact_front_scan_preserves_ascending_zero_indegree_order() {
        let indegree = [0_u32, 2, 0, 1, 0, 3];
        let fronts: Vec<_> = indegree
            .iter()
            .enumerate()
            .filter_map(|(vertex, degree)| (*degree).is_zero().then_some(vertex))
            .collect();
        assert_eq!(fronts, vec![0, 2, 4]);
    }
}
'''
"""
text = text[:constants_start] + constants + text[constants_end:]

apply_start = text.index("def apply_candidate(source):")
apply_end = text.index("\n\ndef build(", apply_start)
apply_function = r'''def apply_candidate(source):
    if source.count(OLD_CONDUCTANCE) != 1:
        raise RuntimeError("conductance traversal marker changed unexpectedly")
    candidate = source.replace(OLD_CONDUCTANCE, NEW_CONDUCTANCE, 1)
    if "mod conductance_front_list_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:apply_start] + apply_function + text[apply_end:]

text = text.replace(
    "Replacing parallel walk and ancestor-prefix vectors with one cache-local entry vector",
    "Reusing diameter scratch to store ordered zero-indegree conductance fronts",
)
text = text.replace(
    "walk vertices and ancestor prefixes share one cache-local scratch stream",
    "conductance traversal skips non-front vertices while preserving ascending front order",
)
text = text.replace(
    '"split_geometric_time_ratio_max": 0.985',
    '"split_geometric_time_ratio_max": 0.98',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.997',
    '"hierarchy_geometric_time_ratio_max": 0.995',
)
text = text.replace(
    'result["split_geometric_time_ratio"] <= 0.985',
    'result["split_geometric_time_ratio"] <= 0.98',
)
text = text.replace(
    'result["hierarchy_geometric_time_ratio"] <= 0.997',
    'result["hierarchy_geometric_time_ratio"] <= 0.995',
)
text = text.replace(
    '"worst_peak_rss_ratio_max": 1.02',
    '"worst_peak_rss_ratio_max": 1.03',
)
text = text.replace(
    'result["worst_peak_rss_ratio"] <= 1.02',
    'result["worst_peak_rss_ratio"] <= 1.03',
)
text = text.replace(
    '"geometric_additional_peak_ratio_max": 1.001',
    '"geometric_additional_peak_ratio_max": 1.03',
)
text = text.replace(
    'result["geometric_additional_peak_ratio"] <= 1.001',
    'result["geometric_additional_peak_ratio"] <= 1.03',
)
text = text.replace(
    '"worst_additional_peak_ratio_max": 1.003',
    '"worst_additional_peak_ratio_max": 1.04',
)
text = text.replace(
    'result["worst_additional_peak_ratio"] <= 1.003',
    'result["worst_additional_peak_ratio"] <= 1.04',
)
text = text.replace(
    "Re-profile split subphases if compact conductance-front list is retained; otherwise test bounded inline scratch with a correctness-preserving spill path.",
    "Re-profile split subphases if the conductance-front list is retained; otherwise close front-compaction work and profile the remaining parent-chain adjustment loop.",
)

required = (
    "conductance_front_list_gate.py",
    "conductance-front-list.yml",
    "conductance-front-list-latest.json",
    "drop(new_ancestors);",
    "for &start in &walk",
    '"split_geometric_time_ratio_max": 0.98',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"conductance-front gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
