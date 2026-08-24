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
GATE_SHA = "64585e5a9d85a1f65aacb8d29f61304ba2c5ebe2"
WORKFLOW_NAME = "dense-endpoint-axis-sort-v2.yml"
DECISION = Path(".ci/performance/dense-endpoint-axis-sort-latest.json")
STATUS = Path(".ci/performance/dense-endpoint-axis-sort-v2-run-status.json")
MONITOR_WORKFLOW = Path(".github/workflows/monitor-dense-endpoint-axis-v2.yml")
MONITOR_SCRIPT = Path("scripts/monitor_dense_endpoint_axis_v2.py")
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"


def request(path):
    req = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cmg-dense-endpoint-v2-monitor",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
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
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


record = {
    "schema_version": 1,
    "monitor": "dense-endpoint-axis-v2",
    "gate_sha": GATE_SHA,
    "monitor_source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "status": "not_found",
}
latest = None
for _ in range(180):
    payload = json_request(
        f"/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_NAME}/runs?per_page=5"
    )
    matches = [item for item in payload.get("workflow_runs", []) if item.get("head_sha") == GATE_SHA]
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
    jobs = json_request(f"/repos/{OWNER}/{REPO}/actions/runs/{latest['id']}/jobs?per_page=100")
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
                raw = request(f"/repos/{OWNER}/{REPO}/actions/jobs/{job['id']}/logs")
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        text = "\n".join(
                            archive.read(name).decode("utf-8", errors="replace")
                            for name in archive.namelist()
                        )
                except zipfile.BadZipFile:
                    text = raw.decode("utf-8", errors="replace")
                failed_logs[str(job["id"])] = text[-60_000:]
            except (urllib.error.HTTPError, urllib.error.URLError) as error:
                failed_logs[str(job["id"])] = f"log retrieval failed: {error!r}"
    if failed_logs:
        record["failed_job_log_tails"] = failed_logs

run(["git", "fetch", "origin", "main"], check=False)
run(["git", "reset", "--hard", "origin/main"], check=False)
record["decision_record_present"] = DECISION.exists()
STATUS.parent.mkdir(parents=True, exist_ok=True)
STATUS.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
MONITOR_WORKFLOW.unlink(missing_ok=True)
MONITOR_SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "ci: record dense endpoint-axis v2 status"])
for _ in range(10):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push dense endpoint-axis v2 monitor result")
