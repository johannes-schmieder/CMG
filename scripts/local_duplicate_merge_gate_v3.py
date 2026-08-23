from pathlib import Path
import subprocess

SOURCE_COMMIT = "e192923a5f8d3dd28c65d8fb57165684e060f54c"
SOURCE_PATH = "scripts/local_duplicate_merge_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("local_duplicate_merge_gate.py", "local_duplicate_merge_gate_v3.py")
text = text.replace("local-duplicate-merge.yml", "local-duplicate-merge-v3.yml")

old_loop = '''                for edge_index in group_start..group_end {
                    compensated_add(&mut sum, &mut correction, raw[edge_index].weight);
                }
'''
new_loop = '''                for edge in &raw[group_start..group_end] {
                    compensated_add(&mut sum, &mut correction, edge.weight);
                }
'''
if text.count(old_loop) != 1:
    raise SystemExit("historical range-index merge loop changed unexpectedly")
text = text.replace(old_loop, new_loop, 1)

old_helper_boundary = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

fn write_merged_edge(
'''
new_helper_boundary = '''fn sort_compact_edge_endpoints(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
}

#[cfg(test)]
fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    sort_compact_edge_endpoints(raw);
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end]
                .sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}

fn write_merged_edge(
'''
if text.count(old_helper_boundary) != 1:
    raise SystemExit("historical endpoint-helper boundary changed unexpectedly")
text = text.replace(old_helper_boundary, new_helper_boundary, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/local-duplicate-merge-v2.yml").unlink(missing_ok=True)
Path("scripts/local_duplicate_merge_gate_v2.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "local_duplicate_merge_gate_v3.py",
    "local-duplicate-merge-v3.yml",
    "for edge in &raw[group_start..group_end]",
    "#[cfg(test)]\nfn sort_compact_edges_two_stage",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"fully repaired local-merge gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
