from pathlib import Path
import subprocess

SOURCE_COMMIT = "17af329a369aec793024e2c0d2c7b1f5ceec3033"
SOURCE_PATH = "scripts/recompute_forest_ancestor_prefix_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "recompute_forest_ancestor_prefix_gate.py",
    "recompute_forest_ancestor_prefix_gate_v2.py",
)
text = text.replace(
    "recompute-forest-ancestor-prefix.yml",
    "recompute-forest-ancestor-prefix-v2.yml",
)

candidate_open = "candidate_block = r'''UPDATE_HELPER = r'''"
candidate_open_fixed = 'candidate_block = r"""UPDATE_HELPER = r\'\'\''
candidate_close = "\n'''\ntext = text[:start] + candidate_block + text[end:]"
candidate_close_fixed = '\n"""\ntext = text[:start] + candidate_block + text[end:]'
update_open = "update_function = r'''def update_documents(result):"
update_open_fixed = 'update_function = r"""def update_documents(result):'
update_close = "\n'''\ntext = text[:update_start] + update_function + text[update_end:]"
update_close_fixed = '\n"""\ntext = text[:update_start] + update_function + text[update_end:]'

for old, new, name in (
    (candidate_open, candidate_open_fixed, "candidate block opening"),
    (candidate_close, candidate_close_fixed, "candidate block closing"),
    (update_open, update_open_fixed, "document block opening"),
    (update_close, update_close_fixed, "document block closing"),
):
    if text.count(old) != 1:
        raise SystemExit(f"{name} marker changed unexpectedly")
    text = text.replace(old, new, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/inline-forest-walk-run-status.json",
    ".ci/performance/inline-forest-walk-v2-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/recompute-forest-ancestor-prefix.yml").unlink(missing_ok=True)
Path("scripts/recompute_forest_ancestor_prefix_gate.py").unlink(missing_ok=True)
for stale in (
    ".ci/performance/inline-forest-walk-run-status.json",
    ".ci/performance/inline-forest-walk-v2-run-status.json",
    ".ci/performance/recompute-ancestor-prefix-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("ancestor-prefix v2 cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "recompute_forest_ancestor_prefix_gate_v2.py",
    "recompute-forest-ancestor-prefix-v2.yml",
    'candidate_block = r"""UPDATE_HELPER',
    'update_function = r"""def update_documents',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired ancestor-prefix gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
