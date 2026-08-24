from pathlib import Path
import subprocess

SOURCE_COMMIT = "2870771df17286578e03f68e83206bd8c869d02b"
SOURCE_PATH = "scripts/reuse_csr_row_counts_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    'WORKFLOW = Path(".github/workflows/reuse-csr-row-counts.yml")',
    'WORKFLOW = Path(".github/workflows/reuse-csr-row-counts-v3.yml")',
    1,
)
text = text.replace(
    'SCRIPT = Path("scripts/reuse_csr_row_counts_gate.py")',
    'SCRIPT = Path("scripts/reuse_csr_row_counts_gate_v3.py")',
    1,
)

old_median = '''fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}
'''
new_median = '''fn median<T: Ord + Copy>(mut values: Vec<T>) -> T {
    values.sort_unstable();
    values[values.len() / 2]
}
'''
if text.count(old_median) != 1:
    raise SystemExit("CSR benchmark median helper changed unexpectedly")
text = text.replace(old_median, new_median, 1)

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
    "for stale in (\n"
    "    \"scripts/reuse_csr_row_counts_gate.py\",\n"
    "    \"scripts/reuse_csr_row_counts_gate_v2.py\",\n"
    "    \".github/workflows/reuse-csr-row-counts-v2.yml\",\n"
    "):\n"
    "    Path(stale).unlink(missing_ok=True)\n"
)
if text.count(old_cleanup) != 1:
    raise SystemExit("CSR row-count cleanup marker changed unexpectedly")
text = text.replace(old_cleanup, new_cleanup, 1)

required = (
    "reuse_csr_row_counts_gate_v3.py",
    "reuse-csr-row-counts-v3.yml",
    "fn median<T: Ord + Copy>",
    '"-D",\n            "warnings"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired CSR plan gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
