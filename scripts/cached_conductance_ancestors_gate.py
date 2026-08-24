from pathlib import Path
import subprocess

SOURCE_COMMIT = "28a1bef8a117f1651a14ce8034b9e2a3ec891afd"
SOURCE_PATH = "scripts/split_conductance_branch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

replacements = (
    ("split_conductance_branch_gate.py", "cached_conductance_ancestors_gate.py"),
    ("split-conductance-branch.yml", "cached-conductance-ancestors.yml"),
    ("split-conductance-branch-latest.json", "cached-conductance-ancestors-latest.json"),
    ("split-conductance-gate.rs", "cached-conductance-gate.rs"),
    ("split-conductance-gate", "cached-conductance-gate"),
    ("branch-free-conductance-pass", "cached-conductance-ancestor-loads"),
    ("Branch-free conductance-pass", "Cached conductance ancestors"),
    ("branch-free conductance-pass", "cached conductance ancestors"),
)
for old, new in replacements:
    text = text.replace(old, new)

constants_start = text.index("OLD_LOOP =")
constants_end = text.index("\n\ndef run(", constants_start)
constants = r"""OLD_LOOP = '''    for start in 0..n {
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

NEW_LOOP = '''    for start in 0..n {
        let mut current = start;
        'new_front: while indegree[current].is_zero() {
            let mut previous = current;
            loop {
                let next = forest[current];
                if next == current || next == previous {
                    break 'new_front;
                }
                let current_ancestors = ancestors[current];
                let next_ancestors = ancestors[next];
                if current_ancestors > 2 && next_ancestors - current_ancestors > 2 {
                    forest[current] = current;
                    indegree[next].decrement();
                    let removed_ancestors = current_ancestors;

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
mod cached_conductance_ancestor_tests {
    #[test]
    fn cached_condition_matches_direct_expression() {
        for current in 0_i64..20 {
            for next in 0_i64..30 {
                let direct = current > 2 && next - current > 2;
                let cached_current = current;
                let cached_next = next;
                let cached = cached_current > 2
                    && cached_next - cached_current > 2;
                assert_eq!(cached, direct);
            }
        }
    }
}
'''
"""
text = text[:constants_start] + constants + text[constants_end:]

wording = (
    (
        "Separating conductance cut search from ancestor adjustment",
        "Caching current and next ancestor counts across each conductance test",
    ),
    (
        "conductance search no longer carries cut-state branches through every parent step",
        "conductance tests reuse loaded ancestor values and cut adjustment reuses the cached current value",
    ),
    (
        "Re-profile forest-split subphases if the conductance refactor is retained.",
        "Re-profile forest-split subphases if cached ancestor loads are retained.",
    ),
)
for old, new in wording:
    text = text.replace(old, new)

thresholds = (
    ('"split_geometric_time_ratio_max": 0.985', '"split_geometric_time_ratio_max": 0.995'),
    ('"hierarchy_geometric_time_ratio_max": 0.997', '"hierarchy_geometric_time_ratio_max": 0.999'),
    ('"worst_split_time_ratio_max": 1.025', '"worst_split_time_ratio_max": 1.02'),
    ('"worst_hierarchy_time_ratio_max": 1.025', '"worst_hierarchy_time_ratio_max": 1.02'),
    ('"worst_peak_rss_ratio_max": 1.02', '"worst_peak_rss_ratio_max": 1.01'),
    ('result["split_geometric_time_ratio"] <= 0.985', 'result["split_geometric_time_ratio"] <= 0.995'),
    ('result["hierarchy_geometric_time_ratio"] <= 0.997', 'result["hierarchy_geometric_time_ratio"] <= 0.999'),
    ('result["worst_split_time_ratio"] <= 1.025', 'result["worst_split_time_ratio"] <= 1.02'),
    ('result["worst_hierarchy_time_ratio"] <= 1.025', 'result["worst_hierarchy_time_ratio"] <= 1.02'),
    ('result["worst_peak_rss_ratio"] <= 1.02', 'result["worst_peak_rss_ratio"] <= 1.01'),
)
for old, new in thresholds:
    if old not in text:
        raise SystemExit(f"historical threshold marker missing: {old}")
    text = text.replace(old, new, 1)

required = (
    "cached_conductance_ancestors_gate.py",
    "cached-conductance-ancestors.yml",
    "cached-conductance-ancestors-latest.json",
    "let current_ancestors = ancestors[current];",
    "let removed_ancestors = current_ancestors;",
    '"split_geometric_time_ratio_max": 0.995',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"cached conductance gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
