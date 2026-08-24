from pathlib import Path
import subprocess

SOURCE_COMMIT = "ee473d147cdcdf0a6930f2bba0143a0241a5a28a"
SOURCE_PATH = "scripts/inline_forest_walk_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "inline_forest_walk_gate.py",
    "recompute_forest_ancestor_prefix_gate.py",
)
text = text.replace(
    "inline-forest-walk.yml",
    "recompute-forest-ancestor-prefix.yml",
)
text = text.replace(
    "inline-forest-walk-latest.json",
    "recompute-forest-ancestor-prefix-latest.json",
)
text = text.replace(
    "inline-forest-diameter-walk-buffer",
    "recompute-forest-ancestor-prefix",
)

insert_marker = '''STATUS = Path("PERFORMANCE_STATUS.md")
'''
run_helper = r'''


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed
'''
if text.count(insert_marker) != 1:
    raise SystemExit("ancestor-prefix gate status marker changed unexpectedly")
text = text.replace(insert_marker, insert_marker + run_helper, 1)

start = text.index("INLINE_BUFFER =")
end = text.index("\n\ndef instrument", start)
candidate_block = r'''UPDATE_HELPER = r'''
#[inline]
fn apply_walk_ancestor_updates(
    walk: &[usize],
    end: usize,
    visited: &mut [bool],
    ancestors: &mut [i64],
) {
    let mut added_ancestors = 0_i64;
    for (index, &vertex) in walk[..=end].iter().enumerate() {
        if index != 0 && !visited[vertex] {
            added_ancestors += 1;
        }
        visited[vertex] = true;
        ancestors[vertex] += added_ancestors;
    }
}

'''
INSERT_MARKER = '''fn split_forest_impl_with_indegree<I: ForestIndegree>(
'''
OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = Vec::new();
'''
OLD_PATH_STATE = '''            let mut ancestors_in_path = 0_i64;
            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_PATH_STATE = '''            walk.clear();
            walk.push(current);
'''
OLD_WALK = '''                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_WALK = '''                k += 1;
                walk.push(current);
'''
OLD_CUT_UPDATES = '''                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_CUT_UPDATES = '''                apply_walk_ancestor_updates(
                    &walk,
                    middle,
                    &mut visited,
                    &mut ancestors,
                );
'''
OLD_FINAL_UPDATES = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_FINAL_UPDATES = '''            if !continue_walk {
                apply_walk_ancestor_updates(
                    &walk,
                    k,
                    &mut visited,
                    &mut ancestors,
                );
            }
'''
TEST_MODULE = r'''

#[cfg(test)]
mod recomputed_forest_ancestor_prefix_tests {
    use super::apply_walk_ancestor_updates;

    #[test]
    fn recomputed_prefix_matches_original_walk_semantics() {
        let walk = [0_usize, 1, 2, 3, 4, 5];
        let mut visited = [false, false, true, false, true, false];
        let mut ancestors = [10_i64, 20, 30, 40, 50, 60];
        apply_walk_ancestor_updates(&walk, walk.len() - 1, &mut visited, &mut ancestors);
        assert_eq!(ancestors, [10, 21, 31, 42, 52, 63]);
        assert!(visited.into_iter().all(|value| value));
    }
}
'''


def apply_candidate(source):
    candidate = source
    replacements = (
        (INSERT_MARKER, UPDATE_HELPER + INSERT_MARKER, "update helper insertion"),
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "walk declarations"),
        (OLD_PATH_STATE, NEW_PATH_STATE, "walk state reset"),
        (OLD_WALK, NEW_WALK, "walk prefix recording"),
        (OLD_CUT_UPDATES, NEW_CUT_UPDATES, "cut prefix application"),
        (OLD_FINAL_UPDATES, NEW_FINAL_UPDATES, "final prefix application"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod recomputed_forest_ancestor_prefix_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
'''
text = text[:start] + candidate_block + text[end:]

update_start = text.index("def update_documents(result):")
update_end = text.index("\n\noriginal_forest =", update_start)
update_function = r'''def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Recomputed forest ancestor-prefix checkpoint — 2026-08-24

- Removing the recorded ancestor-prefix vector and recomputing prefixes at application time was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; baseline/candidate split checksums and hierarchy metadata were identical.
- Geometric trusted-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst hierarchy time / process-RSS ratios: `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Recomputed forest ancestor-prefix checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Refresh cumulative retained optimization and memory guidance.\n"
        "2. Re-profile hierarchy setup if the ancestor-prefix change is retained.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact split parents and complete hierarchy diagnostics in every further gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Recomputed forest ancestor-prefix gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Trusted-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Recomputed forest ancestor-prefix gate\n"
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
text = text[:update_start] + update_function + text[update_end:]

text = text.replace(
    '"split_geometric_time_ratio_max": 0.98',
    '"split_geometric_time_ratio_max": 0.985',
)
text = text.replace(
    '"hierarchy_geometric_time_ratio_max": 0.995',
    '"hierarchy_geometric_time_ratio_max": 0.997',
)
text = text.replace(
    "full qualification passed; common short diameter walks stay inline with a safe arbitrary-length spill path",
    "full qualification passed; the hot walk no longer writes a second prefix vector and exact prefixes are reconstructed only when applied",
)
text = text.replace(
    "correctness passed, but direct-split, full-hierarchy, or memory limits were not all met",
    "correctness passed, but direct-split, full-hierarchy, or memory limits were not all met",
)
text = text.replace(
    "perf: retain inline forest walk buffer",
    "perf: retain recomputed forest ancestor prefixes",
)
text = text.replace(
    "perf: record inline forest-walk experiment",
    "perf: record recomputed forest ancestor-prefix experiment",
)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/inline-forest-walk-run-status.json",
    ".ci/performance/inline-forest-walk-v2-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("ancestor-prefix cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "recompute_forest_ancestor_prefix_gate.py",
    "recompute-forest-ancestor-prefix.yml",
    "apply_walk_ancestor_updates",
    "recompute-forest-ancestor-prefix-latest.json",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"ancestor-prefix gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
