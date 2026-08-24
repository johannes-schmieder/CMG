from pathlib import Path
import subprocess

ROOT = Path.cwd()
PREPARED = Path("scripts/branchless_ancestor_recording_gate.py")
WRAPPER = Path("scripts/execute_branchless_ancestor_recording_gate.py")
WORKFLOW = Path(".github/workflows/branchless-ancestor-recording.yml")

namespace = {
    "__name__": "branchless_ancestor_prepared",
    "__file__": str(PREPARED),
}
prepared_source = PREPARED.read_text()
exec(compile(prepared_source, str(PREPARED), "exec"), namespace)
generated = namespace.get("text")
if not isinstance(generated, str):
    raise SystemExit("prepared branchless ancestor gate did not expose generated text")

exec(
    compile(generated, str(PREPARED), "exec"),
    {"__name__": "__main__", "__file__": str(PREPARED)},
)

# The generated gate commits its numerical decision and deletes the prepared
# script/workflow. Add one small follow-up checkpoint that removes this wrapper.
subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=True)
subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=ROOT, check=True)
WRAPPER.unlink(missing_ok=True)
WORKFLOW.unlink(missing_ok=True)
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
completed = subprocess.run(
    ["git", "commit", "-m", "ci: clean branchless ancestor gate wrapper"],
    cwd=ROOT,
)
if completed.returncode == 0:
    for _ in range(5):
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
        raise SystemExit("failed to push branchless gate wrapper cleanup")
