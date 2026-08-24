from pathlib import Path
import subprocess

SOURCE_COMMIT = "69418e045f9dc04d7125fb821e7a428e0b83be00"
SOURCE_PATH = "scripts/bounded_ancestor_prefix_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "bounded_ancestor_prefix_gate.py",
    "bounded_ancestor_prefix_gate_v3.py",
)
text = text.replace(
    "bounded-ancestor-prefix.yml",
    "bounded-ancestor-prefix-v3.yml",
)

text = text.replace("CANDIDATE = r'''", 'CANDIDATE = r"""', 1)
text = text.replace(
    "'''\ntext = text[:start] + CANDIDATE + text[end:]",
    '"""\ntext = text[:start] + CANDIDATE + text[end:]',
    1,
)
text = text.replace(
    "UPDATE = r'''def update_documents",
    'UPDATE = r"""def update_documents',
    1,
)
text = text.replace(
    "'''\ntext = text[:update_start] + UPDATE + text[update_end:]",
    '"""\ntext = text[:update_start] + UPDATE + text[update_end:]',
    1,
)

old_test = "        assert_eq!(prefix[8_usize.min(6)], 4);\n"
new_test = "        assert_eq!(prefix[6], 4);\n"
if text.count(old_test) != 1:
    raise SystemExit("bounded-prefix constant-min test marker changed unexpectedly")
text = text.replace(old_test, new_test, 1)

required = (
    "bounded_ancestor_prefix_gate_v3.py",
    "bounded-ancestor-prefix-v3.yml",
    'CANDIDATE = r"""',
    "assert_eq!(prefix[6], 4);",
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"bounded-prefix v3 gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
