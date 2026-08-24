from pathlib import Path
import subprocess

BASE_COMMIT = "ca871b25ba10645f6fde29d2e668939d9977636d"
BASE_PATH = "scripts/direct_compact_forest_labels_gate.py"
text = subprocess.check_output(
    ["git", "show", f"{BASE_COMMIT}:{BASE_PATH}"],
    text=True,
)

replacements = (
    (
        'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels.yml")',
        'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels-v8.yml")',
        "workflow path",
    ),
    (
        'SCRIPT = Path("scripts/direct_compact_forest_labels_gate.py")',
        'SCRIPT = Path("scripts/direct_compact_forest_labels_gate_v8.py")',
        "script path",
    ),
    (
        "label as usize < aggregate_count",
        "(label as usize) < aggregate_count",
        "compact-label cast",
    ),
    (
        "FOREST_TYPE_MARKER = '''pub struct ForestGrouping {\n'''\n",
        "FOREST_TYPE_MARKER = '''/// The complete diagnostic result of one CMG Steiner-group construction.\n#[derive(Debug, Clone, PartialEq)]\npub struct ForestGrouping {\n'''\n",
        "forest grouping documented marker",
    ),
)
for old, new, name in replacements:
    if text.count(old) != 1:
        raise SystemExit(f"{name} marker changed unexpectedly")
    text = text.replace(old, new, 1)

old_enum = "FOREST_TYPE_INSERT = '''pub(crate) enum ForestAggregationLabels {\n    Compact(Vec<u32>),\n    Native(Vec<usize>),\n}\n\n'''\n"
new_enum = "FOREST_TYPE_INSERT = '''/// Internal aggregate-label storage produced by hierarchy construction.\n#[derive(Debug)]\npub(crate) enum ForestAggregationLabels {\n    Compact(Vec<u32>),\n    Native(Vec<usize>),\n}\n\n#[cfg(test)]\nimpl ForestAggregationLabels {\n    fn equals_native(&self, other: &[usize]) -> bool {\n        match self {\n            Self::Compact(labels) => {\n                labels.len() == other.len()\n                    && labels\n                        .iter()\n                        .zip(other)\n                        .all(|(&left, &right)| left as usize == right)\n            }\n            Self::Native(labels) => labels == other,\n        }\n    }\n}\n\n#[cfg(test)]\nimpl PartialEq<&[usize]> for ForestAggregationLabels {\n    fn eq(&self, other: &&[usize]) -> bool {\n        self.equals_native(other)\n    }\n}\n\n#[cfg(test)]\nimpl PartialEq<Vec<usize>> for ForestAggregationLabels {\n    fn eq(&self, other: &Vec<usize>) -> bool {\n        self.equals_native(other)\n    }\n}\n\n'''\n"
if text.count(old_enum) != 1:
    raise SystemExit("direct compact-label enum marker changed unexpectedly")
text = text.replace(old_enum, new_enum, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "for stale in (\n"
    "    \"scripts/direct_compact_forest_labels_gate_v7.py\",\n"
    "    \".github/workflows/direct-compact-forest-labels-v7.yml\",\n"
    "):\n"
    "    Path(stale).unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("direct compact-label cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "direct_compact_forest_labels_gate_v8.py",
    "direct-compact-forest-labels-v8.yml",
    "FOREST_TYPE_MARKER = '''/// The complete diagnostic result",
    "/// Internal aggregate-label storage",
    "impl PartialEq<&[usize]> for ForestAggregationLabels",
    "(label as usize) < aggregate_count",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"corrected compact-label gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
