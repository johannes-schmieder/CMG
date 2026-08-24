from pathlib import Path
import subprocess

SOURCE_COMMIT = "926d352c60a6918ab32e2d4cfedcab75fcb57643"
SOURCE_PATH = "scripts/internal_serial_heavy_edge_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "internal_serial_heavy_edge_gate.py",
    "internal_serial_heavy_edge_gate_v2.py",
)
text = text.replace(
    "internal-serial-heavy-edge.yml",
    "internal-serial-heavy-edge-v2.yml",
)
if text.count("options.clone()") != 3:
    raise SystemExit("expected three benchmark-only CmgOptions clones")
text = text.replace("options.clone()", "options")

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/internal-serial-heavy-edge.yml").unlink(missing_ok=True)
Path("scripts/internal_serial_heavy_edge_gate.py").unlink(missing_ok=True)
Path(".ci/performance/internal-heavy-edge-run-status.json").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("heavy-edge gate cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "internal_serial_heavy_edge_gate_v2.py",
    "internal-serial-heavy-edge-v2.yml",
    "let serial = CmgPreconditioner::build(&graph, options)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired heavy-edge gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
