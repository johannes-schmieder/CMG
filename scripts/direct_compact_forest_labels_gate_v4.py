from pathlib import Path
import subprocess

HISTORICAL_COMMIT = "ca871b25ba10645f6fde29d2e668939d9977636d"
HISTORICAL_SCRIPT = "scripts/direct_compact_forest_labels_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{HISTORICAL_COMMIT}:{HISTORICAL_SCRIPT}"],
    text=True,
)

text = text.replace(
    'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels.yml")',
    'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels-v4.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/direct_compact_forest_labels_gate.py")',
    'SCRIPT = Path("scripts/direct_compact_forest_labels_gate_v4.py")',
    1,
)

old_enum = '''FOREST_TYPE_INSERT = '''pub(crate) enum ForestAggregationLabels {
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

'''
'''
new_enum = '''FOREST_TYPE_INSERT = '''pub(crate) enum ForestAggregationLabels {
    Compact(Vec<u32>),
    Native(Vec<usize>),
}

impl ForestAggregationLabels {
    #[cfg(test)]
    fn into_native(self) -> Vec<usize> {
        match self {
            Self::Compact(labels) => labels
                .into_iter()
                .map(|label| label as usize)
                .collect(),
            Self::Native(labels) => labels,
        }
    }
}

'''
'''
if text.count(old_enum) != 1:
    raise SystemExit("historical compact-label enum marker changed unexpectedly")
text = text.replace(old_enum, new_enum, 1)

old_cast = "label as usize < aggregate_count"
new_cast = "(label as usize) < aggregate_count"
if text.count(old_cast) != 1:
    raise SystemExit("historical compact-label cast marker changed unexpectedly")
text = text.replace(old_cast, new_cast, 1)

old_test_hook = '''    if "mod direct_compact_forest_label_tests" not in forest_candidate:
        forest_candidate += FOREST_TEST
'''
new_test_hook = '''    compatibility_replacements = (
        (
            "assert_eq!(labels, complete.labels());",
            "assert_eq!(labels.into_native(), complete.labels());",
            "aggregation-only test",
        ),
        (
            "assert_eq!(trusted_labels, checked_labels);",
            "assert_eq!(trusted_labels.into_native(), checked_labels);",
            "trusted-component test",
        ),
    )
    for old, new, name in compatibility_replacements:
        if forest_candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        forest_candidate = forest_candidate.replace(old, new, 1)
    if "mod direct_compact_forest_label_tests" not in forest_candidate:
        forest_candidate += FOREST_TEST
'''
if text.count(old_test_hook) != 1:
    raise SystemExit("historical compact-label test hook changed unexpectedly")
text = text.replace(old_test_hook, new_test_hook, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "for stale in (\n"
    "    \"scripts/direct_compact_forest_labels_gate.py\",\n"
    "    \"scripts/direct_compact_forest_labels_gate_v2.py\",\n"
    "    \"scripts/direct_compact_forest_labels_gate_v3.py\",\n"
    "    \".github/workflows/direct-compact-forest-labels.yml\",\n"
    "):\n"
    "    Path(stale).unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("historical compact-label cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "direct_compact_forest_labels_gate_v4.py",
    "direct-compact-forest-labels-v4.yml",
    "fn into_native(self) -> Vec<usize>",
    "labels.into_native()",
    "(label as usize) < aggregate_count",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"corrected compact-label gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
