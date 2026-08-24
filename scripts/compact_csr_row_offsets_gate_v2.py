from pathlib import Path

BASE = Path("scripts/compact_csr_row_offsets_gate.py")
text = BASE.read_text()

text = text.replace(
    'WORKFLOW = Path(".github/workflows/compact-csr-row-offsets.yml")',
    'WORKFLOW = Path(".github/workflows/compact-csr-row-offsets-v2.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/compact_csr_row_offsets_gate.py")',
    'SCRIPT = Path("scripts/compact_csr_row_offsets_gate_v2.py")',
    1,
)

old_open = "source_patch = r'''ROW_OFFSETS_INSERT_MARKER"
new_open = 'source_patch = r"""ROW_OFFSETS_INSERT_MARKER'
if text.count(old_open) != 1:
    raise SystemExit("compact CSR source-patch opening delimiter changed unexpectedly")
text = text.replace(old_open, new_open, 1)

old_close = '''    return candidate
'''
text = text[:start] + source_patch + text[end:]
'''
new_close = '''    return candidate
"""
text = text[:start] + source_patch + text[end:]
'''
if text.count(old_close) != 1:
    raise SystemExit("compact CSR source-patch closing delimiter changed unexpectedly")
text = text.replace(old_close, new_close, 1)

old_cleanup = "WORKFLOW.unlink(missing_ok=True)\nSCRIPT.unlink(missing_ok=True)\n"
new_cleanup = (
    "WORKFLOW.unlink(missing_ok=True)\n"
    "SCRIPT.unlink(missing_ok=True)\n"
    "Path(\".github/workflows/compact-csr-row-offsets.yml\").unlink(missing_ok=True)\n"
    "Path(\"scripts/compact_csr_row_offsets_gate.py\").unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("compact CSR cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "compact_csr_row_offsets_gate_v2.py",
    "compact-csr-row-offsets-v2.yml",
    'source_patch = r"""ROW_OFFSETS_INSERT_MARKER',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired compact CSR gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
