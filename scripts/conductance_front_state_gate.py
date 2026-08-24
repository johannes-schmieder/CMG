from pathlib import Path
import subprocess

SOURCE_COMMIT = "28a1bef8a117f1651a14ce8034b9e2a3ec891afd"
SOURCE_PATH = "scripts/split_conductance_branch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

replacements = (
    ("split_conductance_branch_gate.py", "conductance_front_state_gate.py"),
    ("split-conductance-branch.yml", "conductance-front-state.yml"),
    ("split-conductance-branch-latest.json", "conductance-front-state-latest.json"),
    ("split-conductance-gate.rs", "conductance-front-state-gate.rs"),
    ("split-conductance-gate", "conductance-front-state-gate"),
    ("branch-free-conductance-pass", "conductance-front-state"),
    ("Branch-free conductance-pass", "Conductance front-state"),
    ("branch-free conductance pass", "conductance front-state"),
)
for old, new in replacements:
    text = text.replace(old, new)

constants_start = text.index("OLD_LOOP =")
constants_end = text.index("\n\ndef run(", constants_start)
constants = r"""OLD_TRAIT = '''    fn decrement(&mut self);
'''
NEW_TRAIT = '''    fn decrement(&mut self);
    fn decrement_and_is_zero(&mut self) -> bool;
'''

OLD_IMPL = '''            #[inline]
            fn decrement(&mut self) {
                *self = self.checked_sub(1).expect("forest indegree invariant");
            }
'''
NEW_IMPL = '''            #[inline]
            fn decrement(&mut self) {
                *self = self.checked_sub(1).expect("forest indegree invariant");
            }

            #[inline]
            fn decrement_and_is_zero(&mut self) -> bool {
                *self = self.checked_sub(1).expect("forest indegree invariant");
                *self == 0
            }
'''

OLD_LOOP = '''    for start in 0..n {
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
                if ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
                    forest[current] = current;
                    let next_is_front = indegree[next].decrement_and_is_zero();
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

                    if !next_is_front {
                        break 'new_front;
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
mod conductance_front_state_tests {
    use super::ForestIndegree;

    #[test]
    fn decrement_reports_the_new_front_state() {
        let mut one = 1_u32;
        assert!(one.decrement_and_is_zero());
        assert_eq!(one, 0);

        let mut two = 2_u32;
        assert!(!two.decrement_and_is_zero());
        assert_eq!(two, 1);
    }
}
'''
"""
text = text[:constants_start] + constants + text[constants_end:]

old_apply = '''    source = baseline_source
    if source.count(OLD_LOOP) != 1:
        raise RuntimeError("conductance loop source marker changed unexpectedly")
    source = source.replace(OLD_LOOP, NEW_LOOP, 1)
    if "mod branch_free_conductance_tests" not in source:
        source += TEST_MODULE
    SOURCE.write_text(source)
'''
new_apply = '''    source = baseline_source
    for old, new, name in (
        (OLD_TRAIT, NEW_TRAIT, "ForestIndegree trait"),
        (OLD_IMPL, NEW_IMPL, "ForestIndegree implementation"),
        (OLD_LOOP, NEW_LOOP, "conductance loop"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(f"{name} source marker changed unexpectedly")
        source = source.replace(old, new, 1)
    if "mod conductance_front_state_tests" not in source:
        source += TEST_MODULE
    SOURCE.write_text(source)
'''
if text.count(old_apply) != 1:
    raise SystemExit("historical source-application block changed unexpectedly")
text = text.replace(old_apply, new_apply, 1)

wording = (
    (
        "Separating conductance cut search from ancestor adjustment",
        "Returning the new front state directly from the conductance indegree decrement",
    ),
    (
        "conductance search no longer carries cut-state branches through every parent step",
        "conductance cuts avoid reloading the decremented indegree at the next loop header",
    ),
    (
        "Re-profile forest-split subphases if the conductance refactor is retained.",
        "Re-profile forest-split subphases if direct front-state reporting is retained.",
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
    "conductance_front_state_gate.py",
    "conductance-front-state.yml",
    "conductance-front-state-latest.json",
    "fn decrement_and_is_zero(&mut self) -> bool;",
    "let next_is_front = indegree[next].decrement_and_is_zero();",
    '"split_geometric_time_ratio_max": 0.995',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"conductance front-state gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
