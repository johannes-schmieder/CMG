from pathlib import Path

SOURCE = Path("scripts/diameter_front_control_gate.py")
RUNNER = Path("scripts/run_diameter_front_control_gate.py")

namespace = {"__name__": "diameter_front_prepared", "__file__": str(SOURCE)}
source = SOURCE.read_text()
exec(compile(source, str(SOURCE), "exec"), namespace)
text = namespace["text"]

text = text.replace(
    'SCRIPT = Path("scripts/diameter_front_control_gate.py")',
    'SCRIPT = Path("scripts/run_diameter_front_control_gate.py")',
    1,
)
cleanup = '''SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
'''
replacement = '''SCRIPT.unlink(missing_ok=True)
for stale in (
    "scripts/diameter_front_control_gate.py",
    "scripts/diameter_front_control_gate_v2.py",
    ".github/workflows/diameter-front-control-v2.yml",
):
    Path(stale).unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
'''
if text.count(cleanup) != 1:
    raise SystemExit("prepared diameter-front cleanup block changed unexpectedly")
text = text.replace(cleanup, replacement, 1)

required = (
    'WORKFLOW = Path(".github/workflows/diameter-front-control.yml")',
    'SCRIPT = Path("scripts/run_diameter_front_control_gate.py")',
    "'diameter_front: while",
    "apply_candidate(baseline_source)",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"direct diameter-front runner missing marker: {marker}")

compiled = compile(text, str(RUNNER), "exec")
exec(compiled, {"__name__": "__main__", "__file__": str(RUNNER)})
