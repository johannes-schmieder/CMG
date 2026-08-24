import json
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TARGET_SCRIPT = Path("scripts/zipped_ancestor_updates_gate_v2.py")
TARGET_WORKFLOW = Path(".github/workflows/zipped-ancestor-updates-v2.yml")
RECOVERY_SCRIPT = Path("scripts/recover_zipped_ancestor_updates_v2.py")
RECOVERY_WORKFLOW = Path(
    ".github/workflows/recover-zipped-ancestor-updates-v2.yml"
)
RECOVERY_RECORD = Path(
    ".ci/performance/zipped-ancestor-updates-recovery-v2.json"
)
DECISION_RECORD = Path(
    ".ci/performance/zipped-ancestor-updates-latest.json"
)


def run(command, *, check=True, timeout=None):
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
    "recovery": "zipped-ancestor-updates-v2",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip(),
    "target_present": TARGET_SCRIPT.exists(),
    "status": "not_run",
}

if not TARGET_SCRIPT.exists():
    record["status"] = (
        "target_already_resolved"
        if DECISION_RECORD.exists()
        else "target_missing_without_decision"
    )
else:
    completed = run(
        ["python", TARGET_SCRIPT],
        check=False,
        timeout=16_000,
    )
    record["target_returncode"] = completed.returncode
    record["target_output"] = completed.stdout[-200_000:]
    record["status"] = (
        "target_completed"
        if completed.returncode == 0
        else "target_failed"
    )

run(["git", "fetch", "origin", "main"], check=False)
run(["git", "reset", "--hard", "origin/main"], check=False)
record["decision_present"] = DECISION_RECORD.exists()
if DECISION_RECORD.exists():
    record["decision"] = json.loads(DECISION_RECORD.read_text())
record["resolved"] = bool(record["decision_present"])

RECOVERY_RECORD.parent.mkdir(parents=True, exist_ok=True)
RECOVERY_RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
for path in (
    TARGET_SCRIPT,
    TARGET_WORKFLOW,
    RECOVERY_SCRIPT,
    RECOVERY_WORKFLOW,
):
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
commit = run(
    ["git", "commit", "-m", "ci: record repaired zipped gate recovery"],
    check=False,
)
if commit.returncode == 0:
    for _ in range(5):
        run(["git", "pull", "--rebase", "origin", "main"])
        pushed = run(
            ["git", "push", "origin", "HEAD:main"],
            check=False,
        )
        if pushed.returncode == 0:
            break
    else:
        raise SystemExit("failed to push repaired zipped gate recovery")

if not record["resolved"]:
    raise SystemExit(1)
