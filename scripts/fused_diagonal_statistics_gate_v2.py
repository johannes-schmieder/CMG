from pathlib import Path
import subprocess

SOURCE_COMMIT = "89c559f5fb2c14fc91e4fad5c53e18a7e0cb39f9"
SOURCE_PATH = "scripts/fused_merge_diagonal_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)

text = text.replace("fused_merge_diagonal_gate.py", "fused_diagonal_statistics_gate_v2.py")
text = text.replace("fused-merge-diagonal.yml", "fused-diagonal-statistics-v2.yml")
text = text.replace("fused-merge-diagonal-latest.json", "fused-diagonal-statistics-latest.json")
text = text.replace("fused-canonical-merge-diagonal", "fused-diagonal-statistics")
text = text.replace("Fused merge-diagonal", "Fused diagonal-statistics")
text = text.replace("fused merge-diagonal", "fused diagonal-statistics")
text = text.replace("Fused merge and diagonal", "Fused diagonal statistics")

old_stable = '''    stable = ("case", "scale", "vertices", "edges", "repetitions")
    reference = baseline_samples[0]
'''
new_stable = '''    stable = ("case", "scale", "vertices", "repetitions")
    stable += (
        ("raw_edges", "retained_edges")
        if kind == "graph"
        else ("edges",)
    )
    reference = baseline_samples[0]
'''
if text.count(old_stable) != 1:
    raise SystemExit("historical compare metadata block changed unexpectedly")
text = text.replace(old_stable, new_stable, 1)

old_specs = '''    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in specs:
        result["graph_cases"][name] = compare("graph", baseline, candidate, arguments, name)
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
'''
new_specs = '''    graph_specs = (
        ("unique-1m", ["unique", "1000000", "3"]),
        ("duplicates-4-1m", ["duplicates-4", "250000", "3"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "3"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "3"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in graph_specs:
        result["graph_cases"][name] = compare("graph", baseline, candidate, arguments, name)
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
'''
if text.count(old_specs) != 1:
    raise SystemExit("historical benchmark specification block changed unexpectedly")
text = text.replace(old_specs, new_specs, 1)

start = text.index("OLD_PREFIX =")
end = text.index("\n\ndef update_documents", start)
source_patch = r"""OLD_STATS = '''        let diagonal_nnz = diagonal.iter().filter(|degree| **degree != 0.0).count();
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
        let operator_norm_bound = 2.0 * diagonal.iter().copied().fold(0.0, f64::max);
'''
NEW_STATS = '''        let mut diagonal_nnz = 0_usize;
        let mut maximum_degree = 0.0_f64;
        for &degree in &diagonal {
            diagonal_nnz += usize::from(degree != 0.0);
            maximum_degree = maximum_degree.max(degree);
        }
        let matrix_nnz = diagonal_nnz + 2 * raw.len();
        let operator_norm_bound = 2.0 * maximum_degree;
'''
TEST_MODULE = '''

#[cfg(test)]
mod fused_diagonal_statistics_tests {
    use super::Laplacian;

    #[test]
    fn one_pass_diagonal_statistics_match_expected_values() {
        let graph = Laplacian::from_edges(4, [(0, 1, 2.0), (1, 2, 3.0)]).unwrap();
        assert_eq!(graph.matrix_nnz(), 7);
        assert_eq!(graph.operator_norm_bound().to_bits(), 10.0_f64.to_bits());
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_STATS) != 1:
        raise RuntimeError("diagonal-statistics source marker changed unexpectedly")
    candidate = source.replace(OLD_STATS, NEW_STATS, 1)
    if "mod fused_diagonal_statistics_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
"""
text = text[:start] + source_patch + text[end:]

text = text.replace(
    "one canonical edge pass was removed with stable end-to-end setup gains",
    "one full diagonal-array pass was removed with stable end-to-end setup gains",
)
text = text.replace(
    "the fused pass did not produce a sufficiently consistent measured gain",
    "the fused diagonal-statistics pass did not produce a sufficiently consistent measured gain",
)
text = text.replace(
    "perf: retain fused merge-diagonal construction",
    "perf: retain fused diagonal-statistics scan",
)
text = text.replace(
    "perf: record fused merge-diagonal experiment",
    "perf: record fused diagonal-statistics experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/fused-diagonal-statistics.yml").unlink(missing_ok=True)
Path("scripts/fused_diagonal_statistics_gate.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "fused_diagonal_statistics_gate_v2.py",
    "fused-diagonal-statistics-v2.yml",
    "fused-diagonal-statistics-latest.json",
    "maximum_degree = maximum_degree.max(degree)",
    '"duplicates-16-1.6m"',
    'Path("scripts/fused_diagonal_statistics_gate.py").unlink',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired diagonal-statistics gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
