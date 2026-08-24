from pathlib import Path
import json
import subprocess

ROOT = Path.cwd()
TARGET_SCRIPT = Path("scripts/byte_forest_visited_gate_v2.py")
RECOVERY_SCRIPT = Path("scripts/recover_byte_forest_visited_v2.py")
RECOVERY_WORKFLOW = Path(".github/workflows/recover-byte-forest-visited-v2.yml")
STATUS_RECORD = Path(".ci/performance/byte-forest-visited-recovery.json")


def run(command, *, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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
    "recovery": "byte-forest-visited-v2",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "target_present": TARGET_SCRIPT.exists(),
    "status": "not_run",
}

try:
    if TARGET_SCRIPT.exists():
        run(["python", TARGET_SCRIPT])
        run(["git", "fetch", "origin", "main"])
        record["decision_sha"] = subprocess.check_output(
            ["git", "rev-parse", "origin/main"], cwd=ROOT, text=True
        ).strip()
        record["status"] = "target_completed"
    else:
        record["status"] = "target_already_resolved"
except Exception as error:
    record["status"] = "failure"
    record["error"] = repr(error)
    print(f"byte-visited recovery failed: {error}", flush=True)

# The target script may have committed and pushed from this checkout. Synchronize
# before recording the recovery cleanup.
run(["git", "fetch", "origin", "main"], check=False)
run(["git", "reset", "--hard", "origin/main"], check=False)
STATUS_RECORD.parent.mkdir(parents=True, exist_ok=True)
STATUS_RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
RECOVERY_WORKFLOW.unlink(missing_ok=True)
RECOVERY_SCRIPT.unlink(missing_ok=True)
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
run(["git", "commit", "-m", "ci: record byte-visited gate recovery"])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push byte-visited recovery cleanup")

if record["status"] == "failure":
    raise SystemExit(1)
