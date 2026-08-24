from pathlib import Path
import subprocess

SOURCE_COMMIT = "ee473d147cdcdf0a6930f2bba0143a0241a5a28a"
SOURCE_PATH = "scripts/inline_forest_walk_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "inline_forest_walk_gate.py",
    "inline_forest_walk_gate_v2.py",
)
text = text.replace(
    "inline-forest-walk.yml",
    "inline-forest-walk-v2.yml",
)

insert_marker = '''STATUS = Path("PERFORMANCE_STATUS.md")
'''
run_helper = r'''


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed
'''
if text.count(insert_marker) != 1:
    raise SystemExit("inline gate status marker changed unexpectedly")
text = text.replace(insert_marker, insert_marker + run_helper, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/inline-forest-walk.yml").unlink(missing_ok=True)
Path("scripts/inline_forest_walk_gate.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("inline gate cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "inline_forest_walk_gate_v2.py",
    "inline-forest-walk-v2.yml",
    "def run(command, *, env=None, timeout=7200, check=True):",
    "Path(\"scripts/inline_forest_walk_gate.py\").unlink",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired inline gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
