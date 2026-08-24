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
    "bounded_ancestor_prefix_gate_v2.py",
)
text = text.replace(
    "bounded-ancestor-prefix.yml",
    "bounded-ancestor-prefix-v2.yml",
)

if text.count("CANDIDATE = r'''") != 1:
    raise SystemExit("bounded-prefix candidate delimiter changed unexpectedly")
text = text.replace("CANDIDATE = r'''", 'CANDIDATE = r"""', 1)
old_candidate_close = "'''\ntext = text[:start] + CANDIDATE + text[end:]"
new_candidate_close = '"""\ntext = text[:start] + CANDIDATE + text[end:]'
if text.count(old_candidate_close) != 1:
    raise SystemExit("bounded-prefix candidate closing delimiter changed unexpectedly")
text = text.replace(old_candidate_close, new_candidate_close, 1)

if text.count("UPDATE = r'''def update_documents") != 1:
    raise SystemExit("bounded-prefix update delimiter changed unexpectedly")
text = text.replace(
    "UPDATE = r'''def update_documents",
    'UPDATE = r"""def update_documents',
    1,
)
old_update_close = "'''\ntext = text[:update_start] + UPDATE + text[update_end:]"
new_update_close = '"""\ntext = text[:update_start] + UPDATE + text[update_end:]'
if text.count(old_update_close) != 1:
    raise SystemExit("bounded-prefix update closing delimiter changed unexpectedly")
text = text.replace(old_update_close, new_update_close, 1)

required = (
    "bounded_ancestor_prefix_gate_v2.py",
    "bounded-ancestor-prefix-v2.yml",
    'CANDIDATE = r"""',
    'UPDATE = r"""def update_documents',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired bounded-prefix gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
