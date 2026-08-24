import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TARGET = Path("scripts/refresh_cumulative_performance.py")
WORKFLOW = Path(".github/workflows/diagnose-cumulative-performance.yml")
SCRIPT = Path("scripts/diagnose_cumulative_performance.py")
RECORD = Path(".ci/performance/cumulative-diagnostic.json")

completed = subprocess.run(
    ["python", str(TARGET)],
    cwd=ROOT,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
print(completed.stdout, end="")
record = {
    "schema_version": 1,
    "diagnostic": "cumulative-performance-refresh",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "returncode": completed.returncode,
    "output_tail": completed.stdout[-80_000:],
}

# The target may have succeeded and pushed its own decision. Synchronize before
# preserving only this outer diagnostic record.
subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=False)
subprocess.run(
    ["git", "reset", "--hard", "origin/main"],
    cwd=ROOT,
    check=False,
)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

subprocess.run(
    ["git", "config", "user.name", "github-actions[bot]"],
    cwd=ROOT,
    check=True,
)
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
    ["git", "commit", "-m", "ci: record cumulative benchmark diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(12):
    subprocess.run(
        ["git", "pull", "--rebase", "origin", "main"],
        cwd=ROOT,
        check=True,
    )
    pushed = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=ROOT,
    )
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push cumulative diagnostic")

if completed.returncode != 0:
    raise SystemExit(1)
