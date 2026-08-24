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
GATE_SHA = "5dacf6a1698afcdfcc5ed2119df47a9a8130f11c"
DECISION = Path(".ci/performance/routed-endpoint-axis-sort-latest.json")
STATUS = Path(".ci/performance/routed-endpoint-axis-sort-v3-run-status.json")
WORKFLOW = Path(".github/workflows/monitor-routed-endpoint-axis-v3.yml")
SCRIPT = Path("scripts/monitor_routed_endpoint_axis_v3.py")
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"


def request(path):
    req = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cmg-routed-endpoint-v3-monitor",
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
    "monitor": "routed-endpoint-axis-v3",
    "gate_sha": GATE_SHA,
    "monitor_source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "status": "not_found",
}
latest = None
for _ in range(140):
    payload = json_request(f"/repos/{OWNER}/{REPO}/actions/runs?per_page=100")
    matches = [run for run in payload.get("workflow_runs", []) if run.get("head_sha") == GATE_SHA]
    if matches:
        matches.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        latest = next(
            (
                item
                for item in matches
                if "endpoint-axis" in item.get("name", "").lower()
            ),
            matches[0],
        )
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
WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "ci: record routed endpoint-axis v3 status"])
for _ in range(10):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push routed endpoint-axis v3 monitor result")
