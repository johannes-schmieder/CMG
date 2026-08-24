from pathlib import Path
import subprocess

TEMPLATE_COMMIT = "28a1bef8a117f1651a14ce8034b9e2a3ec891afd"
TEMPLATE_PATH = "scripts/split_conductance_branch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{TEMPLATE_COMMIT}:{TEMPLATE_PATH}"],
    text=True,
)
text = text.replace(
    "split_conductance_branch_gate.py",
    "zipped_ancestor_updates_gate.py",
)
text = text.replace(
    "split-conductance-branch.yml",
    "zipped-ancestor-updates.yml",
)
text = text.replace(
    "split-conductance-branch-latest.json",
    "zipped-ancestor-updates-latest.json",
)
text = text.replace(
    "split-conductance-gate.rs",
    "zipped-ancestor-updates-gate.rs",
)
text = text.replace(
    "split-conductance-gate",
    "zipped-ancestor-updates-gate",
)
text = text.replace("/tmp/cmg-conductance-", "/tmp/cmg-zipped-ancestor-")

start = text.index("OLD_LOOP =")
end = text.index("\n\ndef run", start)
CANDIDATE = r'''OLD_DIAMETER_UPDATE = '''                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_DIAMETER_UPDATE = '''                for (&vertex, &new_ancestor) in walk[..=middle]
                    .iter()
                    .zip(&new_ancestors[..=middle])
                {
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestor;
                }
'''
OLD_TERMINAL_UPDATE = '''                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
'''
NEW_TERMINAL_UPDATE = '''                for (&vertex, &new_ancestor) in
                    walk[..=k].iter().zip(&new_ancestors[..=k])
                {
                    ancestors[vertex] += new_ancestor;
                    visited[vertex] = true;
                }
'''
TEST_MODULE = '''

#[cfg(test)]
mod zipped_ancestor_update_tests {
    #[test]
    fn zipped_updates_match_indexed_updates() {
        let walk = [3_usize, 1, 4, 0];
        let deltas = [0_i64, 1, 1, 2];
        let mut indexed = [0_i64; 5];
        let mut zipped = [0_i64; 5];
        for index in 0..walk.len() {
            indexed[walk[index]] += deltas[index];
        }
        for (&vertex, &delta) in walk.iter().zip(&deltas) {
            zipped[vertex] += delta;
        }
        assert_eq!(zipped, indexed);
    }
}
'''


def apply_candidate(source):
    replacements = (
        (
            OLD_DIAMETER_UPDATE,
            NEW_DIAMETER_UPDATE,
            "diameter ancestor-update loop",
        ),
        (
            OLD_TERMINAL_UPDATE,
            NEW_TERMINAL_UPDATE,
            "terminal ancestor-update loop",
        ),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod zipped_ancestor_update_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:start] + CANDIDATE + text[end:]

old_apply = '''    source = baseline_source
    if source.count(OLD_LOOP) != 1:
        raise RuntimeError("conductance loop source marker changed unexpectedly")
    source = source.replace(OLD_LOOP, NEW_LOOP, 1)
    if "mod branch_free_conductance_tests" not in source:
        source += TEST_MODULE
    SOURCE.write_text(source)
'''
new_apply = '''    SOURCE.write_text(apply_candidate(baseline_source))
'''
if text.count(old_apply) != 1:
    raise SystemExit("template source-application block changed unexpectedly")
text = text.replace(old_apply, new_apply, 1)

update_start = text.index("def update_documents(result):")
update_end = text.index("\n\nbaseline_source =", update_start)
UPDATE = r'''def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Zipped ancestor-update checkpoint — 2026-08-24

- Replacing two indexed walk/prefix traversals with exact zipped-slice traversals was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/zipped-ancestor-updates-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Zipped ancestor-update checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile the diameter pass if zipped ancestor updates are retained.\n"
        "2. Continue exact-preserving diameter-loop optimization from the updated profile.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Zipped ancestor-update gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/zipped-ancestor-updates-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Zipped ancestor-update gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")
'''
text = text[:update_start] + UPDATE + text[update_end:]

text = text.replace(
    '"experiment": "branch-free-conductance-pass"',
    '"experiment": "zipped-ancestor-updates"',
)
text = text.replace(
    '"split_geometric_time_ratio_max": 0.985',
    '"split_geometric_time_ratio_max": 0.99',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.997',
    '"hierarchy_geometric_time_ratio_max": 0.998',
)
text = text.replace(
    "full qualification passed; conductance search no longer carries cut-state branches through every parent step",
    "full qualification passed; paired walk and ancestor-prefix updates use one exact bounds-coupled traversal",
)
text = text.replace(
    "perf: retain branch-free conductance split",
    "perf: retain zipped ancestor updates",
)
text = text.replace(
    "perf: record branch-free conductance experiment",
    "perf: record zipped ancestor-update experiment",
)

required = (
    "zipped_ancestor_updates_gate.py",
    "zipped-ancestor-updates.yml",
    "zipped-ancestor-updates-latest.json",
    "zip(&new_ancestors[..=middle])",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"zipped ancestor gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(
    compile(text, str(Path(__file__)), "exec"),
    {"__name__": "__main__", "__file__": __file__},
)
