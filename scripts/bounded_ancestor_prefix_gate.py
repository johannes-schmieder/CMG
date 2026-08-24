from pathlib import Path
import subprocess

SOURCE_COMMIT = "28a1bef8a117f1651a14ce8034b9e2a3ec891afd"
SOURCE_PATH = "scripts/split_conductance_branch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "split_conductance_branch_gate.py",
    "bounded_ancestor_prefix_gate.py",
)
text = text.replace(
    "split-conductance-branch.yml",
    "bounded-ancestor-prefix.yml",
)
text = text.replace(
    "split-conductance-branch-latest.json",
    "bounded-ancestor-prefix-latest.json",
)
text = text.replace(
    "split-conductance-gate.rs",
    "bounded-ancestor-prefix-gate.rs",
)
text = text.replace(
    "split-conductance-gate",
    "bounded-ancestor-prefix-gate",
)
text = text.replace("/tmp/cmg-conductance-", "/tmp/cmg-bounded-prefix-")

start = text.index("OLD_LOOP =")
end = text.index("\n\ndef run", start)
CANDIDATE = r'''OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut ancestor_prefix = [0_u8; 7];
'''
OLD_PATH_STATE = '''            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
            let mut k = 0_usize;
'''
NEW_PATH_STATE = '''            let mut ancestors_in_path = 0_u8;
            walk.clear();
            walk.push(current);
            ancestor_prefix[0] = 0;
            let mut k = 0_usize;
'''
OLD_RECORDING = '''                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_RECORDING = '''                k += 1;
                walk.push(current);
                ancestors_in_path += u8::from(!visited[current]);
                if k < ancestor_prefix.len() {
                    ancestor_prefix[k] = ancestors_in_path;
                } else {
                    debug_assert!(visited[current]);
                }
'''
OLD_UPDATE = '''                    ancestors[vertex] += new_ancestors[index];
'''
NEW_UPDATE = '''                    ancestors[vertex] += i64::from(ancestor_prefix[index.min(6)]);
'''
TEST_MODULE = '''

#[cfg(test)]
mod bounded_ancestor_prefix_tests {
    #[test]
    fn prefix_table_covers_all_unvisited_diameter_steps() {
        let mut prefix = [0_u8; 7];
        let visited = [false, false, true, false, false, true, false, true, true];
        let mut count = 0_u8;
        for index in 1..visited.len() {
            count += u8::from(!visited[index]);
            if index < prefix.len() {
                prefix[index] = count;
            }
        }
        assert_eq!(prefix, [0, 1, 1, 2, 3, 3, 4]);
        assert_eq!(prefix[8_usize.min(6)], 4);
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "ancestor-prefix declaration"),
        (OLD_PATH_STATE, NEW_PATH_STATE, "ancestor-prefix reset"),
        (OLD_RECORDING, NEW_RECORDING, "ancestor-prefix recording"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_UPDATE) != 2:
        raise RuntimeError("expected two ancestor-prefix application sites")
    candidate = candidate.replace(OLD_UPDATE, NEW_UPDATE)
    if "mod bounded_ancestor_prefix_tests" not in candidate:
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
    raise SystemExit("historical source-application block changed unexpectedly")
text = text.replace(old_apply, new_apply, 1)

update_start = text.index("def update_documents(result):")
update_end = text.index("\n\nbaseline_source =", update_start)
UPDATE = r'''def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Bounded ancestor-prefix checkpoint — 2026-08-24

- Replacing the dynamic `Vec<i64>` walk-prefix stream with a fixed seven-byte table was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/bounded-ancestor-prefix-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Bounded ancestor-prefix checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if the bounded prefix is retained.\n"
        "2. Continue exact-preserving diameter-loop optimization from the updated profile.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Bounded ancestor-prefix gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/bounded-ancestor-prefix-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Bounded ancestor-prefix gate\n"
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
    '"experiment": "bounded-ancestor-prefix"',
)
text = text.replace(
    '"split_geometric_time_ratio_max": 0.985',
    '"split_geometric_time_ratio_max": 0.985',
)
text = text.replace(
    "full qualification passed; conductance search no longer carries cut-state branches through every parent step",
    "full qualification passed; the hot diameter walk records all possible new-ancestor prefixes in seven stack bytes",
)
text = text.replace(
    "correctness passed, but split, hierarchy, or memory gates were not all met",
    "correctness passed, but split, hierarchy, or memory gates were not all met",
)
text = text.replace(
    "perf: retain branch-free conductance split",
    "perf: retain bounded ancestor prefixes",
)
text = text.replace(
    "perf: record branch-free conductance experiment",
    "perf: record bounded ancestor-prefix experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("bounded-prefix cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "bounded_ancestor_prefix_gate.py",
    "bounded-ancestor-prefix.yml",
    "bounded-ancestor-prefix-latest.json",
    "ancestor_prefix[index.min(6)]",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"bounded-prefix gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
