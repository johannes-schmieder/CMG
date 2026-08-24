from pathlib import Path

BASE = Path("scripts/compact_csr_row_offsets_gate.py")
text = BASE.read_text()

text = text.replace(
    'WORKFLOW = Path(".github/workflows/compact-csr-row-offsets.yml")',
    'WORKFLOW = Path(".github/workflows/compact-csr-row-offsets-v4.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/compact_csr_row_offsets_gate.py")',
    'SCRIPT = Path("scripts/compact_csr_row_offsets_gate_v4.py")',
    1,
)

old_open = "source_patch = r'''ROW_OFFSETS_INSERT_MARKER"
new_open = 'source_patch = r"""ROW_OFFSETS_INSERT_MARKER'
if text.count(old_open) != 1:
    raise SystemExit("compact CSR source-patch opening delimiter changed unexpectedly")
text = text.replace(old_open, new_open, 1)

old_close = "    return candidate\n'''\ntext = text[:start] + source_patch + text[end:]\n"
new_close = '    return candidate\n"""\ntext = text[:start] + source_patch + text[end:]\n'
if text.count(old_close) != 1:
    raise SystemExit("compact CSR source-patch closing delimiter changed unexpectedly")
text = text.replace(old_close, new_close, 1)

cleanup_start = text.index('old_cleanup = "WORKFLOW.unlink(missing_ok=True)')
cleanup_end = text.index("\nrequired = (", cleanup_start)
cleanup_code = '''cleanup_anchor = "WORKFLOW.unlink(missing_ok=True)\\nSCRIPT.unlink(missing_ok=True)\\n"
cleanup_extension = (
    cleanup_anchor
    + "for stale in (\\n"
    + "    \\\"scripts/reuse_csr_row_counts_gate.py\\\",\\n"
    + "    \\\"scripts/reuse_csr_row_counts_gate_v2.py\\\",\\n"
    + "    \\\"scripts/reuse_csr_row_counts_gate_v3.py\\\",\\n"
    + "    \\\"scripts/compact_csr_row_offsets_gate.py\\\",\\n"
    + "    \\\"scripts/compact_csr_row_offsets_gate_v2.py\\\",\\n"
    + "    \\\"scripts/compact_csr_row_offsets_gate_v3.py\\\",\\n"
    + "    \\\".github/workflows/compact-csr-row-offsets.yml\\\",\\n"
    + "    \\\".github/workflows/compact-csr-row-offsets-v2.yml\\\",\\n"
    + "    \\\".github/workflows/compact-csr-row-offsets-v3.yml\\\",\\n"
    + "):\\n"
    + "    Path(stale).unlink(missing_ok=True)\\n"
)
if cleanup_anchor in text:
    text = text.replace(cleanup_anchor, cleanup_extension, 1)
'''
text = text[:cleanup_start] + cleanup_code + text[cleanup_end:]

for marker in (
    "compact_csr_row_offsets_gate_v4.py",
    "compact-csr-row-offsets-v4.yml",
    'source_patch = r"""ROW_OFFSETS_INSERT_MARKER',
    "cleanup_extension",
):
    if marker not in text:
        raise SystemExit(f"compact CSR v4 gate missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
