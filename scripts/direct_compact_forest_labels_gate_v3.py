from pathlib import Path

BASE = Path("scripts/direct_compact_forest_labels_gate.py")
text = BASE.read_text()

old_script = 'SCRIPT = Path("scripts/direct_compact_forest_labels_gate.py")'
new_script = 'SCRIPT = Path("scripts/direct_compact_forest_labels_gate_v3.py")'
if text.count(old_script) != 1:
    raise SystemExit("direct compact-label script marker changed unexpectedly")
text = text.replace(old_script, new_script, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "Path(\"scripts/direct_compact_forest_labels_gate.py\").unlink(missing_ok=True)\n"
    "Path(\"scripts/direct_compact_forest_labels_gate_v2.py\").unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("direct compact-label cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "ForestAggregationLabels",
    "direct_compact_forest_labels_gate_v3.py",
    "direct-compact-forest-labels-latest.json",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"direct compact-label gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
