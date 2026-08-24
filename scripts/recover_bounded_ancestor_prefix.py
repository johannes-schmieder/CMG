import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TARGET = Path("scripts/bounded_ancestor_prefix_gate.py")
TARGET_WORKFLOW = Path(".github/workflows/bounded-ancestor-prefix.yml")
RECOVERY = Path("scripts/recover_bounded_ancestor_prefix.py")
RECOVERY_WORKFLOW = Path(".github/workflows/recover-bounded-ancestor-prefix.yml")
DECISION = Path(".ci/performance/bounded-ancestor-prefix-latest.json")
RECORD = Path(".ci/performance/bounded-ancestor-prefix-recovery.json")


def run(command, *, check=True, timeout=18000):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(str(item) for item in command)}"
        )
    return completed


record = {
    "schema_version": 1,
    "recovery": "bounded-ancestor-prefix",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "target_present": TARGET.exists(),
    "decision_present_before": DECISION.exists(),
    "status": "not_run",
}

try:
    if TARGET.exists() and not DECISION.exists():
        completed = run(["python", TARGET], check=False)
        record["target_returncode"] = completed.returncode
        record["target_output_tail"] = completed.stdout[-30000:]
        record["status"] = (
            "target_completed"
            if completed.returncode == 0
            else "target_failed"
        )
    elif DECISION.exists():
        record["status"] = "decision_already_present"
    else:
        record["status"] = "target_missing_without_decision"
except Exception as error:
    record["status"] = "recovery_exception"
    record["error"] = repr(error)

# The target may have committed from this checkout. Synchronize to the remote
# production state before adding a small recovery record and cleanup commit.
run(["git", "fetch", "origin", "main"], check=False)
run(["git", "reset", "--hard", "origin/main"], check=False)
record["decision_present_after"] = DECISION.exists()
if DECISION.exists():
    try:
        record["decision"] = json.loads(DECISION.read_text())
    except Exception as error:
        record["decision_parse_error"] = repr(error)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
for path in (TARGET, TARGET_WORKFLOW, RECOVERY, RECOVERY_WORKFLOW):
    path.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass

run(["git", "config", "user.name", "github-actions[bot]"])
run(
    [
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
)
run(["git", "add", "-A"])
run(["git", "commit", "-m", "ci: record bounded-prefix gate recovery"])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push bounded-prefix recovery record")

if not record.get("decision_present_after", False):
    raise SystemExit(1)
