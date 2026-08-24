from pathlib import Path
import subprocess

SOURCE_COMMIT = "8cece8b67eca8c6dc87ebaf3072e9a53b0edc05d"
SOURCE_PATH = "scripts/fused_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

for old, new in (
    ("fused_walk_ancestor_scratch_gate.py", "preallocate_forest_walk_scratch_gate.py"),
    ("fused-walk-ancestor-scratch.yml", "preallocate-forest-walk-scratch.yml"),
    ("fused-walk-ancestor-scratch-latest.json", "preallocate-forest-walk-scratch-latest.json"),
    ("fused-walk-ancestor-scratch-gate.rs", "preallocate-forest-walk-scratch-gate.rs"),
    ("fused-walk-ancestor-scratch-gate", "preallocate-forest-walk-scratch-gate"),
    ("cmg-fused-walk-", "cmg-preallocated-walk-"),
    ("fused-walk-ancestor-scratch", "preallocated-forest-walk-scratch"),
    ("Fused walk/ancestor scratch", "Preallocated forest-walk scratch"),
    ("fused walk/ancestor scratch", "preallocated forest-walk scratch"),
    ("fused walk-ancestor scratch", "preallocated forest-walk scratch"),
):
    text = text.replace(old, new)


def replace_section(source, start_marker, end_marker, replacement):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[:start] + replacement + source[end:]


constants = """ENTRY_TYPE = '''const DEFAULT_FOREST_WALK_CAPACITY: usize = 16;

'''
INSERT_MARKER = '''trait ForestIndegree: Copy {
'''
OLD_SCRATCH = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_SCRATCH = '''    let mut walk = Vec::with_capacity(DEFAULT_FOREST_WALK_CAPACITY);
    let mut new_ancestors = Vec::with_capacity(DEFAULT_FOREST_WALK_CAPACITY);
'''
OLD_INIT = '''            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_INIT = OLD_INIT
OLD_TERMINATED = '''                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
'''
NEW_TERMINATED = OLD_TERMINATED
OLD_PUSH = '''                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
'''
NEW_PUSH = OLD_PUSH
OLD_CUT = '''                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next].decrement();
                let removed = ancestors[walk[middle]];
                for &vertex in &walk[(middle + 1)..=k] {
                    ancestors[vertex] -= removed;
                }
                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_CUT = OLD_CUT
OLD_TERMINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_TERMINAL = OLD_TERMINAL
TEST_MODULE = '''

#[cfg(test)]
mod preallocated_forest_walk_scratch_tests {
    use super::DEFAULT_FOREST_WALK_CAPACITY;

    #[test]
    fn default_capacity_covers_the_qualified_walk_bound() {
        assert!(DEFAULT_FOREST_WALK_CAPACITY >= 16);
    }
}
'''
"""
text = replace_section(text, "ENTRY_TYPE =", "\n\n\ndef run", constants)

old_check = '''    if "new_ancestors" in candidate:
        raise RuntimeError("separate ancestor-prefix scratch remains")
'''
new_check = '''    if "Vec::with_capacity(DEFAULT_FOREST_WALK_CAPACITY)" not in candidate:
        raise RuntimeError("preallocated scratch constructor missing")
'''
if text.count(old_check) != 1:
    raise SystemExit("historical fused-scratch postcondition changed unexpectedly")
text = text.replace(old_check, new_check, 1)

text = text.replace(
    "Replacing parallel walk and ancestor-prefix vectors with one cache-local entry vector was",
    "Preallocating the existing walk and ancestor-prefix vectors to the qualified 16-entry bound was",
)
text = text.replace(
    "walk vertices and ancestor prefixes share one cache-local scratch stream",
    "the existing exact scratch layout avoids growth reallocations while retaining an arbitrary-length spill path",
)
text = text.replace(
    "one cache-local entry vector",
    "two preallocated exact-layout vectors",
)
text = text.replace('"split_geometric_time_ratio_max": 0.985,', '"split_geometric_time_ratio_max": 0.985,')
text = text.replace('"hierarchy_geometric_time_ratio_max": 0.997,', '"hierarchy_geometric_time_ratio_max": 0.997,')
text = text.replace("perf: retain fused walk-ancestor scratch", "perf: retain preallocated forest-walk scratch")
text = text.replace(
    "perf: record fused walk-ancestor scratch experiment",
    "perf: record preallocated forest-walk scratch experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    "scripts/inline_walk_ancestor_scratch_gate_v3.py",
    ".github/workflows/inline-walk-ancestor-scratch-v3.yml",
    "scripts/inline_walk_ancestor_scratch_gate_v4.py",
    ".github/workflows/inline-walk-ancestor-scratch-v4.yml",
    ".ci/performance/inline-walk-ancestor-scratch-diagnostic.json",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("preallocation gate cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "preallocate_forest_walk_scratch_gate.py",
    "preallocate-forest-walk-scratch.yml",
    "preallocate-forest-walk-scratch-latest.json",
    "DEFAULT_FOREST_WALK_CAPACITY",
    "Vec::with_capacity(DEFAULT_FOREST_WALK_CAPACITY)",
    "preallocated_forest_walk_scratch_tests",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"preallocation gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
