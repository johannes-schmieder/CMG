import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
RECORD = Path(".ci/performance/routed-endpoint-axis-sort-clippy.json")
WORKFLOW = Path(".github/workflows/diagnose-routed-endpoint-axis-clippy.yml")
SCRIPT = Path("scripts/diagnose_routed_endpoint_axis_clippy.py")

OLD_SORT = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}
'''
OLD_CONSTRUCTOR_CALL = '''        sort_compact_edge_endpoints(&mut raw);
'''
OLD_TEST_HELPER_CALL = '''    sort_compact_edge_endpoints(raw);
'''
NEW_SORT = '''const ENDPOINT_AXIS_SORT_MIN_EDGES: usize = 2_000_000;
const ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES: usize = 500_000;
const ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX: usize = 4;

#[inline]
fn should_use_endpoint_axis_sort(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= ENDPOINT_AXIS_SORT_MIN_EDGES
        || (edge_count >= ENDPOINT_AXIS_SORT_DENSE_MIN_EDGES
            && edge_count
                >= vertex_count.saturating_mul(ENDPOINT_AXIS_SORT_MIN_EDGES_PER_VERTEX))
}

fn sort_compact_edge_endpoints(vertex_count: usize, raw: &mut [Edge]) {
    if should_use_endpoint_axis_sort(vertex_count, raw.len()) {
        sort_compact_endpoint_axes(raw);
    } else {
        sort_packed_endpoint_keys(raw);
    }
}

fn sort_packed_endpoint_keys(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

fn sort_compact_endpoint_axes(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(|edge| edge.u);
    let mut start = 0;
    while start < raw.len() {
        let first_endpoint = raw[start].u;
        let mut end = start + 1;
        while end < raw.len() && raw[end].u == first_endpoint {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by_key(|edge| edge.v);
        }
        start = end;
    }
}
'''
TEST_MODULE = '''

#[cfg(test)]
mod routed_endpoint_axis_sort_tests {
    use super::{
        Edge, endpoint_key, should_use_endpoint_axis_sort,
        sort_compact_endpoint_axes,
    };

    #[test]
    fn endpoint_axis_sort_matches_packed_endpoint_order() {
        let mut candidate = vec![
            Edge::from_internal_parts(7, 2, 1.0).unwrap(),
            Edge::from_internal_parts(0, 9, 2.0).unwrap(),
            Edge::from_internal_parts(7, 2, 0.5).unwrap(),
            Edge::from_internal_parts(4, 3, 3.0).unwrap(),
            Edge::from_internal_parts(0, 5, 4.0).unwrap(),
        ];
        let mut reference = candidate.clone();
        sort_compact_endpoint_axes(&mut candidate);
        reference.sort_unstable_by_key(endpoint_key);
        let candidate_keys: Vec<_> = candidate.iter().map(endpoint_key).collect();
        let reference_keys: Vec<_> = reference.iter().map(endpoint_key).collect();
        assert_eq!(candidate_keys, reference_keys);
    }

    #[test]
    fn router_matches_qualified_thresholds() {
        assert!(!should_use_endpoint_axis_sort(1_000_000, 1_500_000));
        assert!(should_use_endpoint_axis_sort(1_500_000, 2_250_000));
        assert!(should_use_endpoint_axis_sort(100_000, 800_000));
        assert!(!should_use_endpoint_axis_sort(1_000_000, 999_999));
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_SORT) != 1:
        raise RuntimeError("sort marker changed")
    if source.count(OLD_CONSTRUCTOR_CALL) != 2:
        raise RuntimeError("constructor calls changed")
    if source.count(OLD_TEST_HELPER_CALL) != 1:
        raise RuntimeError("test helper call changed")
    source = source.replace(OLD_SORT, NEW_SORT, 1)
    source = source.replace(
        OLD_CONSTRUCTOR_CALL,
        "        sort_compact_edge_endpoints(vertex_count, &mut raw);\n",
    )
    source = source.replace(
        OLD_TEST_HELPER_CALL,
        "    sort_packed_endpoint_keys(raw);\n",
        1,
    )
    return source + TEST_MODULE


baseline = SOURCE.read_text()
result = {
    "schema_version": 1,
    "diagnostic": "routed-endpoint-axis-sort-clippy",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
}
try:
    SOURCE.write_text(apply_candidate(baseline))
    subprocess.run(["cargo", "fmt", "--all"], cwd=ROOT, check=True)
    completed = subprocess.run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    result["returncode"] = completed.returncode
    result["output"] = completed.stdout
finally:
    SOURCE.write_text(baseline)
    subprocess.run(["cargo", "fmt", "--all"], cwd=ROOT, check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
subprocess.run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ],
    cwd=ROOT,
    check=True,
)
subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
subprocess.run(
    ["git", "commit", "-m", "ci: record routed endpoint-axis Clippy diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(8):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push routed endpoint-axis Clippy diagnostic")
