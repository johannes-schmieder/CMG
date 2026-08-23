from pathlib import Path
import subprocess

SOURCE_COMMIT = "fc7e594173e79f86b0220aa64f64d28aa7bef61e"
SOURCE_PATH = "scripts/compact_forest_indegree_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "compact_forest_indegree_gate.py",
    "compact_forest_indegree_gate_v2.py",
)
text = text.replace(
    "compact-forest-indegree.yml",
    "compact-forest-indegree-v2.yml",
)

old_logic = '''    if candidate.count(OLD_DECREMENT) != 2:
        raise RuntimeError("expected two indegree decrements")
    candidate = candidate.replace(OLD_DECREMENT, NEW_DECREMENT)
'''
new_logic = '''    decrement_pattern = re.compile(
        r'(?m)^(?P<indent>\\s*)indegree\\[next\\] = indegree\\[next\\]\\n'
        r'\\s*\\.checked_sub\\(1\\)\\n'
        r'\\s*\\.expect\\("forest indegree invariant"\\);\\s*$'
    )
    candidate, decrement_count = decrement_pattern.subn(
        r'\\g<indent>indegree[next].decrement();',
        candidate,
    )
    if decrement_count != 2:
        raise RuntimeError(
            f"expected two indegree decrements, found {decrement_count}"
        )
'''
if text.count(old_logic) != 1:
    raise SystemExit("historical decrement replacement logic changed unexpectedly")
text = text.replace(old_logic, new_logic, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/compact-forest-indegree.yml").unlink(missing_ok=True)
Path("scripts/compact_forest_indegree_gate.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "compact_forest_indegree_gate_v2.py",
    "compact-forest-indegree-v2.yml",
    "decrement_pattern = re.compile",
    "expected two indegree decrements, found",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired compact-indegree gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
