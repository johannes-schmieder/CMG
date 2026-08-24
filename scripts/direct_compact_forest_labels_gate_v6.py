from pathlib import Path
import subprocess

BASE_COMMIT = "ca871b25ba10645f6fde29d2e668939d9977636d"
BASE_PATH = "scripts/direct_compact_forest_labels_gate.py"
text = subprocess.check_output(
    ["git", "show", f"{BASE_COMMIT}:{BASE_PATH}"],
    text=True,
)

old_workflow = 'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels.yml")'
new_workflow = 'WORKFLOW = Path(".github/workflows/direct-compact-forest-labels-v6.yml")'
if text.count(old_workflow) != 1:
    raise SystemExit("direct compact-label workflow marker changed unexpectedly")
text = text.replace(old_workflow, new_workflow, 1)

old_script = 'SCRIPT = Path("scripts/direct_compact_forest_labels_gate.py")'
new_script = 'SCRIPT = Path("scripts/direct_compact_forest_labels_gate_v6.py")'
if text.count(old_script) != 1:
    raise SystemExit("direct compact-label script marker changed unexpectedly")
text = text.replace(old_script, new_script, 1)

old_cast = "label as usize < aggregate_count"
new_cast = "(label as usize) < aggregate_count"
if text.count(old_cast) != 1:
    raise SystemExit("direct compact-label cast marker changed unexpectedly")
text = text.replace(old_cast, new_cast, 1)

old_enum = "FOREST_TYPE_INSERT = '''pub(crate) enum ForestAggregationLabels {\n    Compact(Vec<u32>),\n    Native(Vec<usize>),\n}\n\n'''\n"
new_enum = "FOREST_TYPE_INSERT = '''#[derive(Debug)]\npub(crate) enum ForestAggregationLabels {\n    Compact(Vec<u32>),\n    Native(Vec<usize>),\n}\n\n#[cfg(test)]\nimpl ForestAggregationLabels {\n    fn equals_native(&self, other: &[usize]) -> bool {\n        match self {\n            Self::Compact(labels) => {\n                labels.len() == other.len()\n                    && labels\n                        .iter()\n                        .zip(other)\n                        .all(|(&left, &right)| left as usize == right)\n            }\n            Self::Native(labels) => labels == other,\n        }\n    }\n}\n\n#[cfg(test)]\nimpl PartialEq<&[usize]> for ForestAggregationLabels {\n    fn eq(&self, other: &&[usize]) -> bool {\n        self.equals_native(other)\n    }\n}\n\n#[cfg(test)]\nimpl PartialEq<Vec<usize>> for ForestAggregationLabels {\n    fn eq(&self, other: &Vec<usize>) -> bool {\n        self.equals_native(other)\n    }\n}\n\n'''\n"
if text.count(old_enum) != 1:
    raise SystemExit("direct compact-label enum marker changed unexpectedly")
text = text.replace(old_enum, new_enum, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "for stale in (\n"
    "    \"scripts/direct_compact_forest_labels_gate_v4.py\",\n"
    "    \"scripts/direct_compact_forest_labels_gate_v5.py\",\n"
    "    \".github/workflows/direct-compact-forest-labels-v4.yml\",\n"
    "    \".github/workflows/direct-compact-forest-labels-v5.yml\",\n"
    "):\n"
    "    Path(stale).unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("direct compact-label cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "direct_compact_forest_labels_gate_v6.py",
    "direct-compact-forest-labels-v6.yml",
    "impl PartialEq<&[usize]> for ForestAggregationLabels",
    "(label as usize) < aggregate_count",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"corrected compact-label gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
