import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
HISTORICAL_COMMIT = "af9f615dc01b7058630b92c6c7bb8968cb143f7c"
HISTORICAL_SCRIPT = "scripts/rootless_forest_label_gate.py"
SOURCE = Path("src/forest.rs")
RECORD = Path(".ci/performance/rootless-forest-labels-clippy.json")
WORKFLOW = Path(".github/workflows/diagnose-rootless-forest-labels-clippy.yml")
SCRIPT = Path("scripts/diagnose_rootless_forest_labels_clippy.py")

historical = subprocess.check_output(
    ["git", "show", f"{HISTORICAL_COMMIT}:{HISTORICAL_SCRIPT}"],
    cwd=ROOT,
    text=True,
)
marker = "baseline_source = SOURCE.read_text()"
if marker not in historical:
    raise SystemExit("historical rootless-label execution marker missing")
namespace = {"__name__": "rootless_label_diagnostic"}
exec(compile(historical.split(marker, 1)[0], str(SCRIPT), "exec"), namespace)
apply_candidate = namespace["apply_candidate"]

baseline = SOURCE.read_text()
result = {
    "schema_version": 1,
    "diagnostic": "rootless-forest-labels-clippy",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
}
try:
    SOURCE.write_text(apply_candidate(baseline))
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
    ["git", "commit", "-m", "ci: record rootless-label Clippy diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(5):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push rootless-label Clippy diagnostic")
