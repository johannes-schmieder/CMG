import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/diagnose-bounded-ancestor-prefix-clippy.yml")
SCRIPT = Path("scripts/diagnose_bounded_ancestor_prefix_clippy.py")
RECORD = Path(".ci/performance/bounded-ancestor-prefix-clippy.json")
BASE_COMMIT = "69418e045f9dc04d7125fb821e7a428e0b83be00"
BASE_SCRIPT = "scripts/bounded_ancestor_prefix_gate.py"


def reconstruct_gate_text():
    text = subprocess.check_output(
        ["git", "show", f"{BASE_COMMIT}:{BASE_SCRIPT}"],
        cwd=ROOT,
        text=True,
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
    text = text.replace(
        "        assert_eq!(prefix[8_usize.min(6)], 4);\n",
        "        assert_eq!(prefix[6], 4);\n",
        1,
    )
    return text


def candidate_transform():
    gate = reconstruct_gate_text()
    marker = "baseline_source = SOURCE.read_text()"
    if marker not in gate:
        raise RuntimeError("bounded-prefix execution marker missing")
    namespace = {
        "__name__": "bounded_prefix_diagnostic_definitions",
        "__file__": str(SCRIPT),
    }
    exec(compile(gate.split(marker, 1)[0], str(SCRIPT), "exec"), namespace)
    return namespace["apply_candidate"]


baseline = SOURCE.read_text()
result = {
    "schema_version": 1,
    "diagnostic": "bounded-ancestor-prefix-clippy",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "status": "not_run",
}

try:
    SOURCE.write_text(candidate_transform()(baseline))
    subprocess.run(["cargo", "fmt", "--all"], cwd=ROOT, check=True)
    completed = subprocess.run(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    result["returncode"] = completed.returncode
    result["output"] = completed.stdout
    result["status"] = "captured"
except Exception as error:
    result["status"] = "diagnostic_failure"
    result["error"] = repr(error)
finally:
    SOURCE.write_text(baseline)
    subprocess.run(["cargo", "fmt", "--all"], cwd=ROOT, check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
subprocess.run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ],
    cwd=ROOT,
    check=True,
)
subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
subprocess.run(
    ["git", "commit", "-m", "ci: record bounded-prefix Clippy diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(5):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push bounded-prefix Clippy diagnostic")
