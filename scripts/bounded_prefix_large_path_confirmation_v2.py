from pathlib import Path
import subprocess

SOURCE_COMMIT = "f59956a91ac2c0a99793a8be4d970e583ad31c79"
SOURCE_PATH = "scripts/bounded_prefix_large_path_confirmation.py"

text = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_PATH}"],
    text=True,
)
text = text.replace(
    "bounded_prefix_large_path_confirmation.py",
    "bounded_prefix_large_path_confirmation_v2.py",
)
text = text.replace(
    "bounded-prefix-large-path-confirmation.yml",
    "bounded-prefix-large-path-confirmation-v2.yml",
)

if text.count("writer = r'''def update_documents") != 1:
    raise SystemExit("large-path writer opening delimiter changed unexpectedly")
text = text.replace(
    "writer = r'''def update_documents",
    'writer = r"""def update_documents',
    1,
)
old_close = "'''\ntext = text[:start] + writer + text[end:]"
new_close = '"""\ntext = text[:start] + writer + text[end:]'
if text.count(old_close) != 1:
    raise SystemExit("large-path writer closing delimiter changed unexpectedly")
text = text.replace(old_close, new_close, 1)

cleanup_old = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
'''
cleanup_new = '''WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
Path(".github/workflows/bounded-prefix-large-path-confirmation.yml").unlink(missing_ok=True)
Path("scripts/bounded_prefix_large_path_confirmation.py").unlink(missing_ok=True)
'''
if text.count(cleanup_old) != 1:
    raise SystemExit("large-path cleanup marker changed unexpectedly")
text = text.replace(cleanup_old, cleanup_new, 1)

required = (
    "bounded_prefix_large_path_confirmation_v2.py",
    "bounded-prefix-large-path-confirmation-v2.yml",
    'writer = r"""def update_documents',
    '"path-8m"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"repaired large-path launcher missing marker: {marker}")

compile(text, str(Path(__file__)), "exec")
exec(compile(text, str(Path(__file__)), "exec"), {"__name__": "__main__", "__file__": __file__})
