from pathlib import Path
import subprocess

SOURCE_COMMIT = "37f6507113648b1d49a0487583f4fa416bb23202"
SOURCE_PATH = "scripts/inline_walk_ancestor_scratch_gate_v3.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "inline_walk_ancestor_scratch_gate_v3.py",
    "inline_walk_ancestor_scratch_gate_v4.py",
)
text = text.replace(
    "inline-walk-ancestor-scratch-v3.yml",
    "inline-walk-ancestor-scratch-v4.yml",
)

entry_open = "entry_types = r'''ENTRY_TYPES = '''"
entry_close = "\n'''\n'''\ntext = replace_section(text, \"ENTRY_TYPES =\""
if text.count(entry_open) != 1:
    raise SystemExit("entry-types opening delimiter changed unexpectedly")
if text.count(entry_close) != 1:
    raise SystemExit("entry-types closing delimiter changed unexpectedly")
text = text.replace(entry_open, 'entry_types = r"""ENTRY_TYPES = \'\'\'', 1)
text = text.replace(
    entry_close,
    '\n\'\'\'\n"""\ntext = replace_section(text, "ENTRY_TYPES ="',
    1,
)

test_open = "test_module = r'''TEST_MODULE = '''"
test_close = "\n'''\n'''\ntext = replace_section(text, \"TEST_MODULE =\""
if text.count(test_open) != 1:
    raise SystemExit("test-module opening delimiter changed unexpectedly")
if text.count(test_close) != 1:
    raise SystemExit("test-module closing delimiter changed unexpectedly")
text = text.replace(test_open, 'test_module = r"""TEST_MODULE = \'\'\'', 1)
text = text.replace(
    test_close,
    '\n\'\'\'\n"""\ntext = replace_section(text, "TEST_MODULE ="',
    1,
)

cleanup_marker = '''for stale in (
    ".ci/performance/inline-walk-ancestor-scratch-diagnostic.json",
    "scripts/inline_walk_ancestor_scratch_gate_v2.py",
    ".github/workflows/inline-walk-ancestor-scratch-v2.yml",
):
'''
cleanup_replacement = '''for stale in (
    ".ci/performance/inline-walk-ancestor-scratch-diagnostic.json",
    "scripts/inline_walk_ancestor_scratch_gate_v2.py",
    ".github/workflows/inline-walk-ancestor-scratch-v2.yml",
    "scripts/inline_walk_ancestor_scratch_gate_v3.py",
    ".github/workflows/inline-walk-ancestor-scratch-v3.yml",
):
'''
if text.count(cleanup_marker) != 1:
    raise SystemExit("inline gate stale-cleanup block changed unexpectedly")
text = text.replace(cleanup_marker, cleanup_replacement, 1)

required = (
    "inline_walk_ancestor_scratch_gate_v4.py",
    "inline-walk-ancestor-scratch-v4.yml",
    'entry_types = r"""ENTRY_TYPES',
    'test_module = r"""TEST_MODULE',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"delimiter repair missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
