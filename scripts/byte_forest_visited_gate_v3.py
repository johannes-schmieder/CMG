from pathlib import Path
import subprocess

SOURCE_COMMIT = "27a64fa494762f3c1a13c82ddf8d80c78a6e99e4"
SOURCE_PATH = "scripts/byte_forest_visited_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("byte_forest_visited_gate.py", "byte_forest_visited_gate_v3.py")
text = text.replace("byte-forest-visited.yml", "byte-forest-visited-v3.yml")

start = text.index("OLD_VISITED =")
end = text.index("\n\ndef update_documents", start)
source_patch = r"""OLD_VISITED = '''    let mut visited = vec![false; n];
'''
NEW_VISITED = '''    let mut visited = vec![0_u8; n];
'''
OLD_OUTER = '''        while continue_walk && indegree[current].is_zero() && !visited[current] {
'''
NEW_OUTER = '''        while continue_walk && indegree[current].is_zero() && visited[current] == 0 {
'''
OLD_INNER_WHILE = '''            while k <= 5 || visited[current] {
'''
NEW_INNER_WHILE = '''            while k <= 5 || visited[current] != 0 {
'''
OLD_INNER_IF = '''                if visited[current] {
'''
NEW_INNER_IF = '''                if visited[current] != 0 {
'''
OLD_SET = '''                    visited[vertex] = true;
'''
NEW_SET = '''                    visited[vertex] = 1;
'''
TEST_MODULE = '''

#[cfg(test)]
mod byte_forest_visited_tests {
    use super::split_forest;

    #[test]
    fn byte_visit_path_preserves_deterministic_split() {
        let parent = vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11];
        let split = split_forest(&parent).unwrap();
        assert_eq!(split.len(), parent.len());
        assert!(split.iter().all(|target| *target < split.len()));
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_VISITED, NEW_VISITED, "visited vector"),
        (OLD_OUTER, NEW_OUTER, "outer visit condition"),
        (OLD_INNER_WHILE, NEW_INNER_WHILE, "inner visit loop"),
        (OLD_INNER_IF, NEW_INNER_IF, "inner visit branch"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_SET) != 2:
        raise RuntimeError(
            f"expected two visited assignments, found {candidate.count(OLD_SET)}"
        )
    candidate = candidate.replace(OLD_SET, NEW_SET)
    if "mod byte_forest_visited_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    import math

    return math.exp(sum(math.log(value) for value in values) / len(values))
"""
text = text[:start] + source_patch + text[end:]

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".github/workflows/byte-forest-visited.yml",
    "scripts/byte_forest_visited_gate.py",
    ".github/workflows/byte-forest-visited-v2.yml",
    "scripts/byte_forest_visited_gate_v2.py",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "byte_forest_visited_gate_v3.py",
    "byte-forest-visited-v3.yml",
    "visited[current] == 0",
    "visited[current] != 0",
    "visited[vertex] = 1",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"source-accurate byte gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
