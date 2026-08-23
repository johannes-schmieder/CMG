from pathlib import Path
import subprocess

SOURCE_COMMIT = "fc7e594173e79f86b0220aa64f64d28aa7bef61e"
SOURCE_PATH = "scripts/compact_forest_indegree_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace("compact_forest_indegree_gate.py", "compact_forest_ancestors_gate.py")
text = text.replace("compact-forest-indegree.yml", "compact-forest-ancestors.yml")
text = text.replace("compact-forest-indegree-latest.json", "compact-forest-ancestors-latest.json")
text = text.replace("compact-forest-indegree", "compact-forest-ancestors")
text = text.replace("Compact forest-indegree", "Compact forest-ancestor")
text = text.replace("compact forest-indegree", "compact forest-ancestor")
text = text.replace("forest indegrees", "forest ancestor counts")
text = text.replace("Forest indegrees", "Forest ancestor counts")

start = text.index("OLD_SIGNATURE =")
end = text.index("\n\ndef update_documents", start)
source_patch = r"""TRAIT_MARKER = '''impl_forest_indegree!(u32);
impl_forest_indegree!(usize);

'''
TRAIT_REPLACEMENT = '''impl_forest_indegree!(u32);
impl_forest_indegree!(usize);

trait ForestAncestor: Copy {
    const ZERO: Self;

    fn increment(&mut self);
    fn add_assign(&mut self, value: Self);
    fn subtract_assign(&mut self, value: Self);
    fn greater_than_two(self) -> bool;
    fn difference_greater_than_two(upper: Self, lower: Self) -> bool;
}

macro_rules! impl_forest_ancestor {
    ($type:ty) => {
        impl ForestAncestor for $type {
            const ZERO: Self = 0;

            #[inline]
            fn increment(&mut self) {
                *self += 1;
            }

            #[inline]
            fn add_assign(&mut self, value: Self) {
                *self += value;
            }

            #[inline]
            fn subtract_assign(&mut self, value: Self) {
                *self -= value;
            }

            #[inline]
            fn greater_than_two(self) -> bool {
                self > 2
            }

            #[inline]
            fn difference_greater_than_two(upper: Self, lower: Self) -> bool {
                upper - lower > 2
            }
        }
    };
}

impl_forest_ancestor!(i32);
impl_forest_ancestor!(i64);

'''
OLD_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_indegree::<u32>(parent, validate)
    } else {
        split_forest_impl_with_indegree::<usize>(parent, validate)
    }
}

fn split_forest_impl_with_indegree<I: ForestIndegree>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
'''
NEW_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= i32::MAX as usize {
        split_forest_impl_with_storage::<u32, i32>(parent, validate)
    } else if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_storage::<u32, i64>(parent, validate)
    } else {
        split_forest_impl_with_storage::<usize, i64>(parent, validate)
    }
}

fn split_forest_impl_with_storage<I: ForestIndegree, A: ForestAncestor>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
'''
OLD_ANCESTORS = '''    let mut ancestors = vec![0_i64; n];
'''
NEW_ANCESTORS = '''    let mut ancestors = vec![A::ZERO; n];
'''
OLD_PATH_INIT = '''            let mut ancestors_in_path = 0_i64;
'''
NEW_PATH_INIT = '''            let mut ancestors_in_path = A::ZERO;
'''
OLD_NEW_ZERO = '''            new_ancestors.push(0_i64);
'''
NEW_NEW_ZERO = '''            new_ancestors.push(A::ZERO);
'''
OLD_INCREMENT = '''                    ancestors_in_path += 1;
'''
NEW_INCREMENT = '''                    ancestors_in_path.increment();
'''
OLD_SUBTRACT_VERTEX = '''                    ancestors[vertex] -= removed;
'''
NEW_SUBTRACT_VERTEX = '''                    ancestors[vertex].subtract_assign(removed);
'''
OLD_ADD_VERTEX = '''                    ancestors[vertex] += new_ancestors[index];
'''
NEW_ADD_VERTEX = '''                    ancestors[vertex].add_assign(new_ancestors[index]);
'''
OLD_REMOVED_ZERO = '''            let mut removed_ancestors = 0_i64;
'''
NEW_REMOVED_ZERO = '''            let mut removed_ancestors = A::ZERO;
'''
OLD_CONDITION = '''                if !cut_mode && ancestors[current] > 2 && ancestors[next] - ancestors[current] > 2 {
'''
NEW_CONDITION = '''                if !cut_mode
                    && ancestors[current].greater_than_two()
                    && A::difference_greater_than_two(ancestors[next], ancestors[current])
                {
'''
OLD_SUBTRACT_CURRENT = '''                    ancestors[current] -= removed_ancestors;
'''
NEW_SUBTRACT_CURRENT = '''                    ancestors[current].subtract_assign(removed_ancestors);
'''
TEST_MODULE = '''

#[cfg(test)]
mod compact_forest_ancestor_tests {
    use super::split_forest_impl_with_storage;

    #[test]
    fn compact_and_wide_ancestor_paths_match() {
        let parent = vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11];
        let compact = split_forest_impl_with_storage::<u32, i32>(&parent, true).unwrap();
        let wide = split_forest_impl_with_storage::<u32, i64>(&parent, true).unwrap();
        assert_eq!(compact, wide);
    }
}
'''


def apply_candidate(source):
    replacements = (
        (TRAIT_MARKER, TRAIT_REPLACEMENT, "ancestor trait insertion"),
        (OLD_ROUTER, NEW_ROUTER, "storage router"),
        (OLD_ANCESTORS, NEW_ANCESTORS, "ancestor vector"),
        (OLD_PATH_INIT, NEW_PATH_INIT, "path ancestor initialization"),
        (OLD_NEW_ZERO, NEW_NEW_ZERO, "new-ancestor zero"),
        (OLD_INCREMENT, NEW_INCREMENT, "path ancestor increment"),
        (OLD_SUBTRACT_VERTEX, NEW_SUBTRACT_VERTEX, "vertex ancestor subtraction"),
        (OLD_REMOVED_ZERO, NEW_REMOVED_ZERO, "removed ancestor initialization"),
        (OLD_CONDITION, NEW_CONDITION, "conductance condition"),
        (OLD_SUBTRACT_CURRENT, NEW_SUBTRACT_CURRENT, "current ancestor subtraction"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_ADD_VERTEX) != 2:
        raise RuntimeError("expected two ancestor accumulation sites")
    candidate = candidate.replace(OLD_ADD_VERTEX, NEW_ADD_VERTEX)
    if "mod compact_forest_ancestor_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
"""
text = text[:start] + source_patch + text[end:]

text = text.replace(
    "Monomorphized `u32` forest ancestor counts with a native-width fallback",
    "Monomorphized `i32` forest ancestor counts with an `i64` fallback",
)
text = text.replace(
    "compact forest ancestor counts reduced split bandwidth and path hierarchy time with a native fallback",
    "compact forest ancestor counts reduced split bandwidth and path hierarchy time with a wide fallback",
)
text = text.replace(
    "perf: retain compact forest ancestor counts",
    "perf: retain compact forest ancestor counts",
)
text = text.replace(
    "perf: record compact forest-ancestor experiment",
    "perf: record compact forest-ancestor experiment",
)

required = (
    "compact_forest_ancestors_gate.py",
    "compact-forest-ancestors.yml",
    "split_forest_impl_with_storage::<u32, i32>",
    "difference_greater_than_two",
    "compact_and_wide_ancestor_paths_match",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"compact ancestor gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
