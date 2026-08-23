import json
import math
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SYNC_RECORD = Path(".ci/performance/pcg-profiler-sync.json")


def run(command, *, timeout=7200, check=True):
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
        )
    return completed


def sample(binary, arguments):
    completed = run([str(binary), *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected profiler output: {payloads}")
    payload = payloads[0]
    for key, value in payload.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError(f"non-finite profiler field {key}: {value}")
        if "scaled_difference" in key and isinstance(value, (int, float)):
            if value > 5.0e-10:
                raise RuntimeError(f"profiler parity field {key} is too large: {value}")
        if "bitwise" in key and isinstance(value, bool) and not value:
            raise RuntimeError(f"profiler parity field {key} is false")
    return payload


def aggregate(samples):
    keys = set.intersection(*(set(sample) for sample in samples))
    medians = {}
    stable = {}
    for key in sorted(keys):
        values = [sample[key] for sample in samples]
        if all(isinstance(value, bool) for value in values):
            if any(value != values[0] for value in values):
                raise RuntimeError(f"unstable Boolean profiler field: {key}")
            stable[key] = values[0]
        elif all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            medians[key] = statistics.median(values)
        elif all(value == values[0] for value in values):
            stable[key] = values[0]
    timing = {
        key: value
        for key, value in medians.items()
        if key.endswith("_ns") and value >= 0
    }
    phase_timing = {
        key: value
        for key, value in timing.items()
        if not any(
            token in key
            for token in ("total", "serial", "planned", "median", "elapsed")
        )
    }
    top_phases = sorted(phase_timing.items(), key=lambda item: item[1], reverse=True)[:8]
    return {
        "samples": samples,
        "medians": medians,
        "stable": stable,
        "top_phase_timings_ns": dict(top_phases),
    }


def replace_section(text, heading, replacement, next_heading):
    if heading not in text:
        return text.replace(next_heading, replacement + next_heading, 1)
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[:start] + replacement + text[end:]


sync = json.loads(SYNC_RECORD.read_text())
if not sync.get("retained") or sync.get("validation") != "success":
    print("profiler synchronization is not yet retained; leaving dependent gate armed")
    raise SystemExit(0)

result = {
    "schema_version": 1,
    "experiment": "pcg-phase-profile-post-reductions",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "sync_source_sha": sync.get("source_sha"),
    "status": "not_run",
    "cases": {},
}

try:
    run(["cargo", "fmt", "--all", "--", "--check"])
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
    run(
        [
            "cargo",
            "build",
            "--manifest-path",
            "benchmarks/Cargo.toml",
            "--bin",
            "pcg-phase-profile",
            "--release",
        ]
    )
    binary = Path("benchmarks/target/release/pcg-phase-profile")
    specs = [
        ("path-150k", ["path", "150000", "3", "4"]),
        ("worker-firm-300k", ["worker-firm", "100000", "3", "4"]),
        ("worker-firm-600k", ["worker-firm", "200000", "2", "4"]),
        ("dense-worker-firm-400k", ["dense-worker-firm", "25000", "3", "4"]),
    ]
    for name, arguments in specs:
        samples = [sample(binary, arguments) for _ in range(2)]
        result["cases"][name] = aggregate(samples)
    result["status"] = "success"
    result["decision_reason"] = (
        "fresh phase profiles captured after production norm and dot reductions"
    )
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"post-reduction profiling failed: {error}"
    print(result["decision_reason"], flush=True)

record = Path(".ci/performance/pcg-phase-profile-post-reductions.json")
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

rows = []
for name, case in result.get("cases", {}).items():
    phases = case.get("top_phase_timings_ns", {})
    summary = ", ".join(
        f"`{key}`={value / 1.0e6:.3f} ms" for key, value in list(phases.items())[:4]
    ) or "no phase timing fields"
    rows.append(f"| {name} | {summary} |")
checkpoint = f'''### Post-reduction PCG phase profile — 2026-08-23

- Status: `{result["status"]}`.
- Decision: {result.get("decision_reason", "no decision recorded")}.

| Case | Largest reported phase timings |
|---|---|
''' + ("\n".join(rows) if rows else "| no completed cases | — |") + '''

- The synchronized profiler requires planned-solver parity before reporting timings.
- Machine-readable evidence: `.ci/performance/pcg-phase-profile-post-reductions.json`.

'''
plan_path = Path("PERFORMANCE_PLAN.md")
plan = plan_path.read_text()
plan = replace_section(
    plan,
    "### Post-reduction PCG phase profile — 2026-08-23\n",
    checkpoint,
    "## Current next action\n",
)
plan_path.write_text(plan)

status_path = Path("PERFORMANCE_STATUS.md")
status = status_path.read_text().rstrip()
status_heading = "## Post-reduction PCG phase profile\n"
status_block = (
    "## Post-reduction PCG phase profile\n\n"
    f'- Status: `{result["status"]}`.\n'
    "- Fresh profiles were captured after the retained exact norm-scale and "
    "deterministic fixed-chunk dot optimizations.\n"
    "- Evidence: `.ci/performance/pcg-phase-profile-post-reductions.json`.\n"
)
if status_heading in status:
    start = status.index(status_heading)
    end = status.find("\n## ", start + len(status_heading))
    if end == -1:
        end = len(status)
    status = status[:start] + status_block + status[end:]
else:
    status += "\n\n" + status_block
status_path.write_text(status.rstrip() + "\n")

Path(".github/workflows/profile-post-reductions.yml").unlink(missing_ok=True)
Path("scripts/profile_post_reductions.py").unlink(missing_ok=True)
