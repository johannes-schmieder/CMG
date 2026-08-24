from pathlib import Path
import subprocess

SOURCE_COMMIT = "14a71daed1eb15cae1159543725502019c0f1fb4"
SOURCE_PATH = "scripts/preallocate_forest_walk_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "preallocate_forest_walk_scratch_gate.py",
    "preallocate_forest_walk_scratch_gate_v2.py",
)
text = text.replace(
    "preallocate-forest-walk-scratch.yml",
    "preallocate-forest-walk-scratch-v2.yml",
)

old_test = '''    #[test]
    fn default_capacity_covers_the_qualified_walk_bound() {
        assert!(DEFAULT_FOREST_WALK_CAPACITY >= 16);
    }
'''
new_test = '''    #[test]
    fn default_capacity_is_requested_from_vec() {
        let walk: Vec<usize> = Vec::with_capacity(DEFAULT_FOREST_WALK_CAPACITY);
        assert!(walk.capacity() >= DEFAULT_FOREST_WALK_CAPACITY);
    }
'''
if text.count(old_test) != 1:
    raise SystemExit("preallocation constant test changed unexpectedly")
text = text.replace(old_test, new_test, 1)

cleanup_marker = '''for stale in (
    "scripts/inline_walk_ancestor_scratch_gate_v3.py",
'''
cleanup_replacement = '''for stale in (
    "scripts/preallocate_forest_walk_scratch_gate.py",
    ".github/workflows/preallocate-forest-walk-scratch.yml",
    "scripts/inline_walk_ancestor_scratch_gate_v3.py",
'''
if text.count(cleanup_marker) != 1:
    raise SystemExit("preallocation cleanup marker changed unexpectedly")
text = text.replace(cleanup_marker, cleanup_replacement, 1)

required = (
    "preallocate_forest_walk_scratch_gate_v2.py",
    "preallocate-forest-walk-scratch-v2.yml",
    "default_capacity_is_requested_from_vec",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"preallocation v2 gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
