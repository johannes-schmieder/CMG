from pathlib import Path
import subprocess

SOURCE_COMMIT = "af9f615dc01b7058630b92c6c7bb8968cb143f7c"
SOURCE_PATH = "scripts/rootless_forest_label_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    'WORKFLOW = Path(".github/workflows/rootless-forest-labels.yml")',
    'WORKFLOW = Path(".github/workflows/rootless-forest-labels-v2.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/rootless_forest_label_gate.py")',
    'SCRIPT = Path("scripts/rootless_forest_label_gate_v2.py")',
    1,
)

old_import = "    use super::{forest_component_labels, forest_component_labels_trusted};\n"
new_import = "    use super::forest_component_labels_trusted;\n"
if text.count(old_import) != 1:
    raise SystemExit("rootless-label test import marker changed unexpectedly")
text = text.replace(old_import, new_import, 1)

old_assert = "        assert_eq!(forest_component_labels(&parent).unwrap(), expected);\n"
if text.count(old_assert) != 1:
    raise SystemExit("rootless-label public assertion marker changed unexpectedly")
text = text.replace(old_assert, "", 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "Path(\".github/workflows/rootless-forest-labels.yml\").unlink(missing_ok=True)\n"
    "Path(\"scripts/rootless_forest_label_gate.py\").unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("rootless-label cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "rootless_forest_label_gate_v2.py",
    "rootless-forest-labels-v2.yml",
    "use super::forest_component_labels_trusted;",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired rootless-label gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
