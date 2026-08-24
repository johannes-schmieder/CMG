from pathlib import Path
import subprocess

SOURCE_COMMIT = "5611a83ae50e14528f0761e22cfaeb2d8385aee4"
SOURCE_PATH = "scripts/lsd_radix_sort_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
opening = "candidate_block = r'''OLD_COMPACT = '''"
closing = "\n'''\ntext = text[:candidate_start] + candidate_block + text[candidate_end:]"
if text.count(opening) != 1:
    raise SystemExit("LSD candidate-block opening delimiter changed unexpectedly")
if text.count(closing) != 1:
    raise SystemExit("LSD candidate-block closing delimiter changed unexpectedly")
text = text.replace(
    opening,
    'candidate_block = r"""OLD_COMPACT = \'\'\'',
    1,
)
text = text.replace(
    closing,
    '\n"""\ntext = text[:candidate_start] + candidate_block + text[candidate_end:]',
    1,
)
text = text.replace("lsd_radix_sort_gate.py", "lsd_radix_sort_gate_v2.py")
text = text.replace("lsd-radix-sort.yml", "lsd-radix-sort-v2.yml")

cleanup_marker = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path("scripts/row_bucket_endpoint_sort_gate.py").unlink(missing_ok=True)
try:
'''
cleanup_replacement = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path("scripts/row_bucket_endpoint_sort_gate.py").unlink(missing_ok=True)
Path("scripts/lsd_radix_sort_gate.py").unlink(missing_ok=True)
Path(".github/workflows/lsd-radix-sort.yml").unlink(missing_ok=True)
try:
'''
if text.count(cleanup_marker) != 1:
    raise SystemExit("LSD v2 cleanup marker changed unexpectedly")
text = text.replace(cleanup_marker, cleanup_replacement, 1)

required = (
    'candidate_block = r"""OLD_COMPACT',
    "lsd_radix_sort_gate_v2.py",
    "lsd-radix-sort-v2.yml",
    'Path("scripts/lsd_radix_sort_gate.py").unlink',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired LSD gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
