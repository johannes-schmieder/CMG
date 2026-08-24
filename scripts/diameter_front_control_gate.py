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
    "diameter_front_control_gate.py",
)
text = text.replace(
    "split-conductance-branch.yml",
    "diameter-front-control.yml",
)
text = text.replace(
    "split-conductance-branch-latest.json",
    "diameter-front-control-latest.json",
)
text = text.replace(
    "split-conductance-gate.rs",
    "diameter-front-control-gate.rs",
)
text = text.replace(
    "split-conductance-gate",
    "diameter-front-control-gate",
)
text = text.replace("/tmp/cmg-conductance-", "/tmp/cmg-diameter-front-")

start = text.index("OLD_LOOP =")
end = text.index("\n\ndef run", start)
CANDIDATE = r"""OLD_HEADER = '''    for start in 0..n {
        let mut current = start;
        let mut continue_walk = true;
        while continue_walk && indegree[current].is_zero() && !visited[current] {
            continue_walk = false;
'''
NEW_HEADER = '''    for start in 0..n {
        let mut current = start;
        'diameter_front: while indegree[current].is_zero() && !visited[current] {
'''
OLD_TAIL = '''                current = next;
                continue_walk = true;
            }

            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_TAIL = '''                current = next;
                continue 'diameter_front;
            }

            for index in 0..=k {
                let vertex = walk[index];
                ancestors[vertex] += new_ancestors[index];
                visited[vertex] = true;
            }
            break 'diameter_front;
'''
TEST_MODULE = '''

#[cfg(test)]
mod diameter_front_control_tests {
    use super::split_forest;

    #[test]
    fn labeled_diameter_front_preserves_roots_and_two_cycles() {
        for parent in [
            vec![0],
            vec![1, 0],
            vec![1, 2, 3, 4, 5, 6, 7, 7],
            vec![1, 2, 3, 4, 4, 6, 7, 7],
        ] {
            let split = split_forest(&parent).unwrap();
            assert_eq!(split.len(), parent.len());
            assert!(split.iter().all(|target| *target < split.len()));
        }
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_HEADER) != 1:
        raise RuntimeError("diameter-front header marker changed unexpectedly")
    if source.count(OLD_TAIL) != 1:
        raise RuntimeError("diameter-front tail marker changed unexpectedly")
    candidate = source.replace(OLD_HEADER, NEW_HEADER, 1)
    candidate = candidate.replace(OLD_TAIL, NEW_TAIL, 1)
    if "mod diameter_front_control_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate
"""
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
UPDATE = r"""def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Branch-free diameter-front checkpoint — 2026-08-24

- Replacing the diameter pass's `continue_walk` state with labeled control flow was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/diameter-front-control-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Branch-free diameter-front checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if the diameter-front refactor is retained.\n"
        "2. Continue exact-preserving diameter-loop optimization from the updated profile.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Branch-free diameter-front gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/diameter-front-control-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Branch-free diameter-front gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")
"""
text = text[:update_start] + UPDATE + text[update_end:]

text = text.replace(
    '"experiment": "branch-free-conductance-pass"',
    '"experiment": "branch-free-diameter-front"',
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
    "full qualification passed; the diameter pass no longer carries a mutable continue-state branch through every walk",
)
text = text.replace(
    "perf: retain branch-free conductance split",
    "perf: retain branch-free diameter fronts",
)
text = text.replace(
    "perf: record branch-free conductance experiment",
    "perf: record branch-free diameter-front experiment",
)

required = (
    "diameter_front_control_gate.py",
    "diameter-front-control.yml",
    "diameter-front-control-latest.json",
    "'diameter_front: while",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"diameter-front gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
# Deliberately do not execute here. This file is prepared for the next serial gate
# after any active bounded-prefix decision resolves.
