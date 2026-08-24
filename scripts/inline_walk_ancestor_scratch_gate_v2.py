from pathlib import Path
import subprocess

SOURCE_COMMIT = "0b78ca69b0e605c6fdd4b59b5fbde8f20798f2f0"
SOURCE_PATH = "scripts/inline_walk_ancestor_scratch_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "inline_walk_ancestor_scratch_gate.py",
    "inline_walk_ancestor_scratch_gate_v2.py",
)
text = text.replace(
    "inline-walk-ancestor-scratch.yml",
    "inline-walk-ancestor-scratch-v2.yml",
)

opening = "source_patch = r'''INLINE_TYPES = '''"
closing = "    return candidate\n'''\ntext = text[:start] + source_patch + text[end:]"
if text.count(opening) != 1:
    raise SystemExit("invalid launcher opening delimiter changed unexpectedly")
if text.count(closing) != 1:
    raise SystemExit("invalid launcher closing delimiter changed unexpectedly")
text = text.replace(opening, 'source_patch = r"""INLINE_TYPES = \'\'\'', 1)
text = text.replace(
    closing,
    '    return candidate\n"""\ntext = text[:start] + source_patch + text[end:]',
    1,
)

required = (
    "inline_walk_ancestor_scratch_gate_v2.py",
    "inline-walk-ancestor-scratch-v2.yml",
    'source_patch = r"""INLINE_TYPES',
    'return candidate\n"""',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired inline launcher missing marker: {marker}")

compiled = compile(text, str(Path(__file__)), "exec")
exec(compiled, {"__name__": "__main__", "__file__": __file__})
