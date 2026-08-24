from pathlib import Path
import subprocess

SOURCE_COMMIT = "715ad1ebd0ca8e9cd90c4e20f9aa799e8de2c743"
SOURCE_PATH = "scripts/zipped_ancestor_updates_gate.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "zipped_ancestor_updates_gate.py",
    "zipped_ancestor_updates_gate_v2.py",
)
text = text.replace(
    "zipped-ancestor-updates.yml",
    "zipped-ancestor-updates-v2.yml",
)

opening = "CANDIDATE = r'''OLD_DIAMETER_UPDATE = '''"
fixed_opening = 'CANDIDATE = r"""OLD_DIAMETER_UPDATE = \'\'\''
if text.count(opening) != 1:
    raise SystemExit("historical zipped candidate opening changed unexpectedly")
text = text.replace(opening, fixed_opening, 1)

closing = "    return candidate\n'''\ntext = text[:start] + CANDIDATE + text[end:]"
fixed_closing = '    return candidate\n"""\ntext = text[:start] + CANDIDATE + text[end:]'
if text.count(closing) != 1:
    raise SystemExit("historical zipped candidate closing changed unexpectedly")
text = text.replace(closing, fixed_closing, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".ci/performance/zipped-ancestor-updates-recovery.json").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("historical zipped cleanup block changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "zipped_ancestor_updates_gate_v2.py",
    "zipped-ancestor-updates-v2.yml",
    'CANDIDATE = r"""',
    'return candidate\n"""',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired zipped gate missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(
    compile(text, str(Path(__file__)), "exec"),
    {"__name__": "__main__", "__file__": __file__},
)
