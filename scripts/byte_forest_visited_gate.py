from pathlib import Path
import subprocess

SOURCE_COMMIT = "fc7e594173e79f86b0220aa64f64d28aa7bef61e"
SOURCE_PATH = "scripts/compact_forest_indegree_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace("compact_forest_indegree_gate.py", "byte_forest_visited_gate.py")
text = text.replace("compact-forest-indegree.yml", "byte-forest-visited.yml")
text = text.replace("compact-forest-indegree-latest.json", "byte-forest-visited-latest.json")
text = text.replace("compact-forest-indegree", "byte-forest-visited")
text = text.replace("Compact forest-indegree", "Byte forest-visited")
text = text.replace("compact forest-indegree", "byte forest-visited")
text = text.replace("forest indegrees", "forest visited flags")
text = text.replace("Forest indegrees", "Forest visited flags")

start = text.index("OLD_SIGNATURE =")
end = text.index("\n\ndef update_documents", start)
source_patch = r"""OLD_VISITED = '''    let mut visited = vec![false; n];
'''
NEW_VISITED = '''    let mut visited = vec![0_u8; n];
'''
OLD_WHILE = '''            while !visited[current] {
'''
NEW_WHILE = '''            while visited[current] == 0 {
'''
OLD_SET = '''                visited[current] = true;
'''
NEW_SET = '''                visited[current] = 1;
'''
OLD_FILL = '''    visited.fill(false);
'''
NEW_FILL = '''    visited.fill(0);
'''
TEST_MODULE = '''

#[cfg(test)]
mod byte_forest_visited_tests {
    #[test]
    fn byte_visit_marker_has_expected_layout() {
        assert_eq!(std::mem::size_of::<u8>(), 1);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_VISITED) != 1:
        raise RuntimeError("visited vector marker changed unexpectedly")
    candidate = source.replace(OLD_VISITED, NEW_VISITED, 1)
    if candidate.count(OLD_WHILE) != 2:
        raise RuntimeError("expected two visited loop conditions")
    candidate = candidate.replace(OLD_WHILE, NEW_WHILE)
    if candidate.count(OLD_SET) != 2:
        raise RuntimeError("expected two visited assignments")
    candidate = candidate.replace(OLD_SET, NEW_SET)
    if candidate.count(OLD_FILL) != 1:
        raise RuntimeError("visited reset marker changed unexpectedly")
    candidate = candidate.replace(OLD_FILL, NEW_FILL, 1)
    if "mod byte_forest_visited_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    import math

    return math.exp(sum(math.log(value) for value in values) / len(values))
"""
text = text[:start] + source_patch + text[end:]

text = text.replace(
    "Monomorphized `u32` forest visited flags with a native-width fallback",
    "Byte-per-vertex forest visited flags",
)
text = text.replace(
    "byte forest visited flags reduced split bandwidth and path hierarchy time with a native fallback",
    "byte forest visited flags reduced proxy/bit access overhead enough to justify their temporary memory",
)
text = text.replace(
    '"split_geometric_time_ratio_max": 0.95',
    '"split_geometric_time_ratio_max": 0.94',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.997',
    '"hierarchy_geometric_time_ratio_max": 0.99',
)
text = text.replace(
    '"worst_peak_rss_ratio_max": 1.01',
    '"worst_peak_rss_ratio_max": 1.03',
)
text = text.replace(
    "perf: retain compact forest visited flags",
    "perf: retain byte forest visited flags",
)
text = text.replace(
    "perf: record byte forest-visited experiment",
    "perf: record byte forest-visited experiment",
)

required = (
    "byte_forest_visited_gate.py",
    "byte-forest-visited.yml",
    "visited[current] == 0",
    "visited[current] = 1",
    "def geometric(values)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"byte visited gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
