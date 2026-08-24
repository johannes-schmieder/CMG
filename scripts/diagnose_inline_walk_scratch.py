import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TARGET = Path("scripts/inline_walk_ancestor_scratch_gate.py")
WORKFLOW = Path(".github/workflows/diagnose-inline-walk-scratch.yml")
SCRIPT = Path("scripts/diagnose_inline_walk_scratch.py")
RECORD = Path(".ci/performance/inline-walk-ancestor-scratch-diagnostic.json")

completed = subprocess.run(
    ["python", TARGET],
    cwd=ROOT,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
print(completed.stdout, end="")

# The target can commit/push on success. Refresh and inspect before writing the
# diagnostic cleanup commit.
subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=False)
origin_head = subprocess.check_output(
    ["git", "rev-parse", "origin/main"], cwd=ROOT, text=True
).strip()
result = {
    "schema_version": 1,
    "diagnostic": "inline-walk-ancestor-scratch-launcher",
    "target_returncode": completed.returncode,
    "target_output": completed.stdout,
    "origin_head_after_target": origin_head,
    "decision_record_exists_on_origin": subprocess.run(
        [
            "git",
            "cat-file",
            "-e",
            "origin/main:.ci/performance/inline-walk-ancestor-scratch-latest.json",
        ],
        cwd=ROOT,
    ).returncode
    == 0,
}

subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=ROOT, check=False)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
if completed.returncode != 0:
    Path(".github/workflows/inline-walk-ancestor-scratch.yml").unlink(missing_ok=True)
    TARGET.unlink(missing_ok=True)
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
    ["git", "commit", "-m", "ci: record inline scratch launcher diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(5):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push inline scratch diagnostic")
