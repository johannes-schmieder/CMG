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
    "branchless_ancestor_recording_gate.py",
)
text = text.replace(
    "split-conductance-branch.yml",
    "branchless-ancestor-recording.yml",
)
text = text.replace(
    "split-conductance-branch-latest.json",
    "branchless-ancestor-recording-latest.json",
)
text = text.replace(
    "split-conductance-gate.rs",
    "branchless-ancestor-recording-gate.rs",
)
text = text.replace(
    "split-conductance-gate",
    "branchless-ancestor-recording-gate",
)
text = text.replace("/tmp/cmg-conductance-", "/tmp/cmg-branchless-ancestor-")

start = text.index("OLD_LOOP =")
end = text.index("\n\ndef run", start)
CANDIDATE = r"""OLD_RECORDING = '''                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
'''
NEW_RECORDING = '''                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
'''
TEST_MODULE = '''

#[cfg(test)]
mod branchless_ancestor_recording_tests {
    #[test]
    fn boolean_increment_matches_branching_definition() {
        for visited in [false, true] {
            let branchless = i64::from(u8::from(!visited));
            let branching = if visited { 0 } else { 1 };
            assert_eq!(branchless, branching);
        }
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_RECORDING) != 1:
        raise RuntimeError("ancestor-recording marker changed unexpectedly")
    candidate = source.replace(OLD_RECORDING, NEW_RECORDING, 1)
    if "mod branchless_ancestor_recording_tests" not in candidate:
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
    checkpoint = f'''### Branchless ancestor-recording checkpoint — 2026-08-24

- Replacing the visited branch with a Boolean-to-integer increment was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/branchless-ancestor-recording-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Branchless ancestor-recording checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile forest-split subphases if branchless recording is retained.\n"
        "2. Run the prepared branch-free diameter-front gate.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Branchless ancestor-recording gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/branchless-ancestor-recording-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Branchless ancestor-recording gate\n"
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
    '"experiment": "branchless-ancestor-recording"',
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
    "full qualification passed; ancestor-prefix recording no longer branches on every visited-state observation",
)
text = text.replace(
    "perf: retain branch-free conductance split",
    "perf: retain branchless ancestor recording",
)
text = text.replace(
    "perf: record branch-free conductance experiment",
    "perf: record branchless ancestor-recording experiment",
)

required = (
    "branchless_ancestor_recording_gate.py",
    "branchless-ancestor-recording.yml",
    "branchless-ancestor-recording-latest.json",
    "i64::from(u8::from(!visited[current]))",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"branchless ancestor gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
# Prepared only. Arm after the active bounded-prefix scaling decision resolves.
