from pathlib import Path

BASE = Path("scripts/reuse_csr_row_counts_gate.py")
text = BASE.read_text()

text = text.replace(
    'WORKFLOW = Path(".github/workflows/reuse-csr-row-counts.yml")',
    'WORKFLOW = Path(".github/workflows/reuse-csr-row-counts-v2.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/reuse_csr_row_counts_gate.py")',
    'SCRIPT = Path("scripts/reuse_csr_row_counts_gate_v2.py")',
    1,
)

old_clippy = '''            "--",
            "D",
            "warnings",
'''
new_clippy = '''            "--",
            "-D",
            "warnings",
'''
if text.count(old_clippy) != 1:
    raise SystemExit("CSR benchmark Clippy marker changed unexpectedly")
text = text.replace(old_clippy, new_clippy, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "Path(\"scripts/reuse_csr_row_counts_gate.py\").unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("CSR row-count cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "reuse_csr_row_counts_gate_v2.py",
    "reuse-csr-row-counts-v2.yml",
    '"-D",\n            "warnings"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired CSR row-count gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
