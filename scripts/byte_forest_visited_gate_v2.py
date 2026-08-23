from pathlib import Path
import subprocess

SOURCE_COMMIT = "27a64fa494762f3c1a13c82ddf8d80c78a6e99e4"
SOURCE_PATH = "scripts/byte_forest_visited_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace("byte_forest_visited_gate.py", "byte_forest_visited_gate_v2.py")
text = text.replace("byte-forest-visited.yml", "byte-forest-visited-v2.yml")

old_constants = '''OLD_WHILE = '''            while !visited[current] {
'''
NEW_WHILE = '''            while visited[current] == 0 {
'''
'''
new_constants = '''OLD_WHILE = '''            while !visited[current] {
'''
NEW_WHILE = '''            while visited[current] == 0 {
'''
OLD_COMBINED_WHILE = '''            while indegree[current].is_zero() && !visited[current] {
'''
NEW_COMBINED_WHILE = '''            while indegree[current].is_zero() && visited[current] == 0 {
'''
'''
if text.count(old_constants) != 1:
    raise SystemExit("historical visited condition constants changed unexpectedly")
text = text.replace(old_constants, new_constants, 1)

old_logic = '''    if candidate.count(OLD_WHILE) != 2:
        raise RuntimeError("expected two visited loop conditions")
    candidate = candidate.replace(OLD_WHILE, NEW_WHILE)
'''
new_logic = '''    if candidate.count(OLD_WHILE) != 1:
        raise RuntimeError("first-phase visited condition changed unexpectedly")
    candidate = candidate.replace(OLD_WHILE, NEW_WHILE, 1)
    if candidate.count(OLD_COMBINED_WHILE) != 1:
        raise RuntimeError("second-phase visited condition changed unexpectedly")
    candidate = candidate.replace(
        OLD_COMBINED_WHILE,
        NEW_COMBINED_WHILE,
        1,
    )
'''
if text.count(old_logic) != 1:
    raise SystemExit("historical visited replacement logic changed unexpectedly")
text = text.replace(old_logic, new_logic, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/byte-forest-visited.yml").unlink(missing_ok=True)
Path("scripts/byte_forest_visited_gate.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "byte_forest_visited_gate_v2.py",
    "byte-forest-visited-v2.yml",
    "OLD_COMBINED_WHILE",
    "visited[current] == 0",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired byte-visited gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
