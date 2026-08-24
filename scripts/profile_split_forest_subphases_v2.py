from pathlib import Path

SOURCE = Path("scripts/profile_split_forest_subphases.py")
text = SOURCE.read_text()
text = text.replace(
    "profile_split_forest_subphases.py",
    "profile_split_forest_subphases_v2.py",
)
text = text.replace(
    "profile-split-forest-subphases.yml",
    "profile-split-forest-subphases-v2.yml",
)

bad_line = "                        visited.set(index, visited[index]);\n"
if text.count(bad_line) != 1:
    raise SystemExit("split profiler no-op visit marker changed unexpectedly")
text = text.replace(bad_line, "", 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/profile-split-forest-subphases.yml").unlink(missing_ok=True)
Path("scripts/profile_split_forest_subphases.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("split profiler cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "profile_split_forest_subphases_v2.py",
    "profile-split-forest-subphases-v2.yml",
    "split subphase profiler diverged from production output",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired split profiler missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
