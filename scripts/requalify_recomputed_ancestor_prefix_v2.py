from pathlib import Path
import subprocess

SOURCE_COMMIT = "2b9bab7975a0f2516d1829ee8f09fb36456a8009"
SOURCE_PATH = "scripts/requalify_recomputed_ancestor_prefix.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "requalify_recomputed_ancestor_prefix.py",
    "requalify_recomputed_ancestor_prefix_v2.py",
)
text = text.replace(
    "requalify-recomputed-ancestor-prefix.yml",
    "requalify-recomputed-ancestor-prefix-v2.yml",
)

start = text.index("def load_candidate_transform():")
end = text.index("\n\ndef build", start)
direct_transform = r"""UPDATE_HELPER = '''#[inline]
fn apply_walk_ancestor_updates(
    walk: &[usize],
    end: usize,
    visited: &mut [bool],
    ancestors: &mut [i64],
) {
    let mut added_ancestors = 0_i64;
    for (index, &vertex) in walk[..=end].iter().enumerate() {
        if index != 0 && !visited[vertex] {
            added_ancestors += 1;
        }
        visited[vertex] = true;
        ancestors[vertex] += added_ancestors;
    }
}

'''
INSERT_MARKER = '''fn split_forest_impl_with_indegree<I: ForestIndegree>(
'''
OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = Vec::new();
'''
OLD_PATH_STATE = '''            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_PATH_STATE = '''            walk.clear();
            walk.push(current);
'''
OLD_WALK = '''                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_WALK = '''                k += 1;
                walk.push(current);
'''
OLD_CUT_UPDATES = '''                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_CUT_UPDATES = '''                apply_walk_ancestor_updates(
                    &walk,
                    middle,
                    &mut visited,
                    &mut ancestors,
                );
'''
OLD_FINAL_UPDATES = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_FINAL_UPDATES = '''            if !continue_walk {
                apply_walk_ancestor_updates(
                    &walk,
                    k,
                    &mut visited,
                    &mut ancestors,
                );
            }
'''
TEST_MODULE = '''

#[cfg(test)]
mod recomputed_forest_ancestor_prefix_tests {
    use super::apply_walk_ancestor_updates;

    #[test]
    fn recomputed_prefix_matches_original_walk_semantics() {
        let walk = [0_usize, 1, 2, 3, 4, 5];
        let mut visited = [false, false, true, false, true, false];
        let mut ancestors = [10_i64, 20, 30, 40, 50, 60];
        apply_walk_ancestor_updates(&walk, walk.len() - 1, &mut visited, &mut ancestors);
        assert_eq!(ancestors, [10, 21, 31, 42, 52, 63]);
        assert!(visited.into_iter().all(|value| value));
    }
}
'''


def apply_candidate(source):
    candidate = source
    replacements = (
        (INSERT_MARKER, UPDATE_HELPER + INSERT_MARKER, "update helper insertion"),
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "walk declarations"),
        (OLD_PATH_STATE, NEW_PATH_STATE, "walk state reset"),
        (OLD_WALK, NEW_WALK, "walk prefix recording"),
        (OLD_CUT_UPDATES, NEW_CUT_UPDATES, "cut prefix application"),
        (OLD_FINAL_UPDATES, NEW_FINAL_UPDATES, "final prefix application"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod recomputed_forest_ancestor_prefix_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def load_candidate_transform():
    return apply_candidate
"""
text = text[:start] + direct_transform + text[end:]

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/recompute-ancestor-prefix-v2-run-status.json",
    ".ci/performance/recompute-ancestor-prefix-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/requalify-recomputed-ancestor-prefix.yml").unlink(missing_ok=True)
Path("scripts/requalify_recomputed_ancestor_prefix.py").unlink(missing_ok=True)
for stale in (
    ".ci/performance/recompute-ancestor-prefix-v2-run-status.json",
    ".ci/performance/recompute-ancestor-prefix-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("ancestor-prefix RSS cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "requalify_recomputed_ancestor_prefix_v2.py",
    "requalify-recomputed-ancestor-prefix-v2.yml",
    "def load_candidate_transform():\n    return apply_candidate",
    "apply_walk_ancestor_updates",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired RSS gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
