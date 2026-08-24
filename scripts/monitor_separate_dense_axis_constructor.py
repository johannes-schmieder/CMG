import io
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path.cwd()
OWNER = "johannes-schmieder"
REPO = "CMG"
GATE_SHA = "c78860ad29681558ac6083c5a24e12f8115fdc0d"
WORKFLOW_NAME = "separate-dense-axis-constructor.yml"
DECISION = Path(".ci/performance/separate-dense-axis-constructor-latest.json")
STATUS = Path(".ci/performance/separate-dense-axis-constructor-run-status.json")
WORKFLOW = Path(".github/workflows/monitor-separate-dense-axis-constructor.yml")
SCRIPT = Path("scripts/monitor_separate_dense_axis_constructor.py")
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"


def request(path):
    request_object = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cmg-separate-dense-axis-monitor",
        },
    )
    with urllib.request.urlopen(request_object, timeout=60) as response:
        return response.read()


def json_request(path):
    return json.loads(request(path))


def run(command, *, check=True):
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print("+", " ".join(command), flush=True)
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


record = {
    "schema_version": 1,
    "monitor": "separate-dense-axis-constructor",
    "gate_sha": GATE_SHA,
    "monitor_source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "status": "not_found",
}
latest = None
for _ in range(220):
    payload = json_request(
        f"/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_NAME}/runs?per_page=5"
    )
    matches = [
        item
        for item in payload.get("workflow_runs", [])
        if item.get("head_sha") == GATE_SHA
    ]
    if matches:
        latest = matches[0]
        record["run"] = {
            key: latest.get(key)
            for key in (
                "id",
                "name",
                "head_sha",
                "status",
                "conclusion",
                "created_at",
                "updated_at",
                "html_url",
                "run_attempt",
            )
        }
        print(json.dumps(record["run"], indent=2, sort_keys=True), flush=True)
        if latest.get("status") == "completed":
            break
    time.sleep(15)
else:
    record["status"] = "monitor_timeout"

if latest is not None and latest.get("status") == "completed":
    record["status"] = "completed"
    jobs = json_request(
        f"/repos/{OWNER}/{REPO}/actions/runs/{latest['id']}/jobs?per_page=100"
    )
    record["jobs"] = [
        {
            key: job.get(key)
            for key in (
                "id",
                "name",
                "status",
                "conclusion",
                "started_at",
                "completed_at",
                "html_url",
            )
        }
        for job in jobs.get("jobs", [])
    ]
    failed_logs = {}
    for job in jobs.get("jobs", []):
        if job.get("conclusion") not in ("success", "skipped"):
            try:
                raw = request(
                    f"/repos/{OWNER}/{REPO}/actions/jobs/{job['id']}/logs"
                )
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        text = "\n".join(
                            archive.read(name).decode("utf-8", errors="replace")
                            for name in archive.namelist()
                        )
                except zipfile.BadZipFile:
                    text = raw.decode("utf-8", errors="replace")
                failed_logs[str(job["id"])] = text[-80_000:]
            except (urllib.error.HTTPError, urllib.error.URLError) as error:
                failed_logs[str(job["id"])] = f"log retrieval failed: {error!r}"
    if failed_logs:
        record["failed_job_log_tails"] = failed_logs

run(["git", "fetch", "origin", "main"], check=False)
run(["git", "reset", "--hard", "origin/main"], check=False)
if DECISION.exists():
    decision = json.loads(DECISION.read_text())
    record["decision_record"] = {
        key: decision.get(key)
        for key in (
            "source_sha",
            "experiment",
            "validation",
            "accepted",
            "decision_reason",
            "contraction_geometric_time_ratio",
            "active_contraction_geometric_time_ratio",
            "hierarchy_geometric_time_ratio",
            "active_hierarchy_geometric_time_ratio",
            "worst_control_contraction_time_ratio",
            "worst_control_hierarchy_time_ratio",
            "worst_peak_rss_ratio",
        )
    }
record["decision_record_present"] = DECISION.exists()
STATUS.parent.mkdir(parents=True, exist_ok=True)
STATUS.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
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
run(["git", "commit", "-m", "ci: record separate dense constructor status"])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push separate dense constructor monitor result")
