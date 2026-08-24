import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
HISTORICAL_COMMIT = "ca871b25ba10645f6fde29d2e668939d9977636d"
HISTORICAL_SCRIPT = "scripts/direct_compact_forest_labels_gate.py"
FOREST = Path("src/forest.rs")
COARSEN = Path("src/coarsen.rs")
HIERARCHY = Path("src/hierarchy.rs")
RECORD = Path(".ci/performance/direct-compact-labels-clippy.json")
WORKFLOW = Path(".github/workflows/diagnose-direct-compact-labels-clippy.yml")
SCRIPT = Path("scripts/diagnose_direct_compact_labels_clippy.py")

historical = subprocess.check_output(
    ["git", "show", f"{HISTORICAL_COMMIT}:{HISTORICAL_SCRIPT}"],
    cwd=ROOT,
    text=True,
)
marker = "baseline_forest = FOREST.read_text()"
if marker not in historical:
    raise SystemExit("historical compact-label execution marker missing")
namespace = {"__name__": "direct_compact_label_diagnostic"}
exec(compile(historical.split(marker, 1)[0], str(SCRIPT), "exec"), namespace)
apply_candidate = namespace["apply_candidate"]

baseline_forest = FOREST.read_text()
baseline_coarsen = COARSEN.read_text()
baseline_hierarchy = HIERARCHY.read_text()
result = {
    "schema_version": 1,
    "diagnostic": "direct-compact-forest-labels-clippy",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
}
try:
    forest, coarsen, hierarchy = apply_candidate(
        baseline_forest,
        baseline_coarsen,
        baseline_hierarchy,
    )
    FOREST.write_text(forest)
    COARSEN.write_text(coarsen)
    HIERARCHY.write_text(hierarchy)
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
    FOREST.write_text(baseline_forest)
    COARSEN.write_text(baseline_coarsen)
    HIERARCHY.write_text(baseline_hierarchy)
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
    ["git", "commit", "-m", "ci: record direct compact-label Clippy diagnostic"],
    cwd=ROOT,
    check=True,
)
for _ in range(5):
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
    pushed = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT)
    if pushed.returncode == 0:
        break
else:
    raise SystemExit("failed to push compact-label Clippy diagnostic")
