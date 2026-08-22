import json
import os
from pathlib import Path
import subprocess

ROOT = Path.cwd()
TARGET = Path("/tmp/cmg-hierarchy-allocation-probe")


def run(command, *, env=None, timeout=3600):
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
run(
    [
        "cargo",
        "fmt",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--all",
        "--",
        "--check",
    ]
)
run(
    [
        "cargo",
        "clippy",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--all-targets",
        "--",
        "-D",
        "warnings",
    ]
)
env = os.environ.copy()
env["CARGO_TARGET_DIR"] = str(TARGET)
run(
    [
        "cargo",
        "build",
        "--release",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--bin",
        "hierarchy-alloc",
    ],
    env=env,
)
binary = TARGET / "release" / "hierarchy-alloc"

cases = (
    ("path-100k", ["path", "100000", "3"]),
    ("worker-firm-300k", ["worker-firm", "100000", "3"]),
    ("dense-worker-firm-480k", ["dense-worker-firm", "30000", "3"]),
)
records = {}
for name, arguments in cases:
    completed = run([str(binary), *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected allocation probe output: {payloads}")
    records[name] = payloads[0]

result = {
    "schema_version": 1,
    "probe": "hierarchy-requested-allocation-tracker",
    "commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "validation": "success",
    "cases": records,
}
destination = Path(".ci/performance/hierarchy-allocation-probe.json")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
record_anchor = (
    "- `.ci/performance/inplace-edge-compaction-latest.json`: "
    "rejected in-place edge-compaction experiment.\n"
)
record_entry = (
    "- `.ci/performance/hierarchy-allocation-probe.json`: benchmark-only exact "
    "requested-byte hierarchy allocation probe.\n"
)
if record_entry not in plan:
    if record_anchor not in plan:
        raise RuntimeError("performance record anchor missing")
    plan = plan.replace(record_anchor, record_anchor + record_entry, 1)

marker = "## Current next action\n"
if marker not in plan:
    raise RuntimeError("current-next-action heading missing")
checkpoint = "\n".join(
    [
        "### Hierarchy allocation probe checkpoint — 2026-08-22",
        "",
        "- Added a benchmark-only global counting allocator that resets its peak",
        "  after graph construction and reports additional live requested bytes during",
        "  hierarchy construction, retained hierarchy bytes, and post-drop balance.",
        "- The probe is isolated from production code and supplements, rather than",
        "  replaces, process-level peak-RSS measurements.",
        "- Formatting, benchmark Clippy, release compilation, and representative",
        "  path/worker-firm/dense-worker-firm runs passed.",
        "- Machine-readable evidence:",
        "  `.ci/performance/hierarchy-allocation-probe.json`.",
        "",
        "",
    ]
)
if "### Hierarchy allocation probe checkpoint — 2026-08-22" not in plan:
    plan = plan.replace(marker, checkpoint + marker, 1)
plan_path.write_text(plan)

Path("scripts/run_hierarchy_allocation_probe.py").unlink(missing_ok=True)
Path(".github/workflows/hierarchy-allocation-probe.yml").unlink(missing_ok=True)
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
run(["git", "commit", "-m", "bench: qualify hierarchy allocation probe"])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
