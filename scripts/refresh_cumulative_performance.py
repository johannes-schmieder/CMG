import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
BASELINE_SHA = "b45b252f88925028e3ad9a73a3f75eeab05f6754"
WORKFLOW = Path(".github/workflows/refresh-cumulative-performance.yml")
SCRIPT = Path("scripts/refresh_cumulative_performance.py")
RECORD = Path(".ci/performance/cumulative-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


def run(command, *, cwd=ROOT, env=None, timeout=7200, check=True):
    command = [str(item) for item in command]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
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


def build(root, target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    env["RUSTFLAGS"] = "-C target-cpu=native"
    run(
        ["cargo", "build", "--release", "--bin", "cmg-bench"],
        cwd=root,
        env=env,
    )
    return target / "release" / "cmg-bench"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-cumulative-{tag}.time")
    completed = run(
        [
            "/usr/bin/time",
            "-v",
            "-o",
            time_path,
            binary,
            *arguments,
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def metric_median(samples, key):
    return statistics.median(item[key] for item in samples)


def compare_case(baseline_binary, current_binary, arguments, name):
    baseline_samples = []
    current_samples = []
    schedule = (
        ("baseline", baseline_binary),
        ("current", current_binary),
        ("current", current_binary),
        ("baseline", baseline_binary),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else current_samples).append(
            observation
        )

    common_stable = (
        "case",
        "scale",
        "vertices",
        "edges",
        "rhs_count",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + current_samples:
        for key in common_stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable benchmark metadata for {key}")
        if observation["backward_error"] > 1.0e-8:
            raise RuntimeError(
                f"{name}: uncertified backward error {observation['backward_error']}"
            )

    timing_keys = (
        "graph_build_median_ns",
        "hierarchy_build_median_ns",
        "apply_per_rhs_median_ns",
        "solve_per_rhs_median_ns",
    )
    memory_keys = (
        "cmg_workspace_bytes",
        "pcg_workspace_bytes",
    )
    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in common_stable},
        "baseline_iterations": metric_median(baseline_samples, "iterations"),
        "current_iterations": metric_median(current_samples, "iterations"),
        "baseline_backward_error": max(
            item["backward_error"] for item in baseline_samples
        ),
        "current_backward_error": max(
            item["backward_error"] for item in current_samples
        ),
        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "current_peak_rss_kib": max(
            item["peak_rss_kib"] for item in current_samples
        ),
    }
    result["current_over_baseline_peak_rss"] = (
        result["current_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    for key in timing_keys + memory_keys:
        baseline_value = metric_median(baseline_samples, key)
        current_value = metric_median(current_samples, key)
        result[f"baseline_{key}"] = baseline_value
        result[f"current_{key}"] = current_value
        result[f"current_over_baseline_{key}"] = current_value / baseline_value
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


current_sha = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()
baseline_root = Path("/tmp/cmg-cumulative-baseline")
run(["git", "worktree", "remove", "--force", baseline_root], check=False)
run(["git", "worktree", "add", "--detach", baseline_root, BASELINE_SHA])

result = {
    "schema_version": 1,
    "benchmark": "cumulative-current-versus-frozen-baseline",
    "baseline_sha": BASELINE_SHA,
    "source_sha": current_sha,
    "status": "not_run",
    "cases": {},
}
try:
    baseline_binary = build(
        baseline_root,
        Path("/tmp/cmg-cumulative-baseline-target"),
    )
    current_binary = build(
        ROOT,
        Path("/tmp/cmg-cumulative-current-target"),
    )
    specs = (
        ("path-100k", ["path", "100000", "5", "8"]),
        ("worker-firm-100k", ["worker-firm", "100000", "5", "8"]),
        ("path-500k", ["path", "500000", "3", "8"]),
        ("worker-firm-500k", ["worker-firm", "500000", "3", "8"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline_binary,
            current_binary,
            arguments,
            name,
        )

    ratio_fields = (
        "graph_build_median_ns",
        "hierarchy_build_median_ns",
        "apply_per_rhs_median_ns",
        "solve_per_rhs_median_ns",
        "cmg_workspace_bytes",
        "pcg_workspace_bytes",
    )
    for field in ratio_fields:
        values = [
            case[f"current_over_baseline_{field}"]
            for case in result["cases"].values()
        ]
        result[f"geometric_current_over_baseline_{field}"] = geometric(values)
        result[f"worst_current_over_baseline_{field}"] = max(values)
    rss_values = [
        case["current_over_baseline_peak_rss"]
        for case in result["cases"].values()
    ]
    result["geometric_current_over_baseline_peak_rss"] = geometric(rss_values)
    result["worst_current_over_baseline_peak_rss"] = max(rss_values)
    result["status"] = "success"
finally:
    run(["git", "worktree", "remove", "--force", baseline_root], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

if result["status"] == "success":
    rows = []
    for name, case in result["cases"].items():
        rows.append(
            f"| {name} | "
            f"{case['current_over_baseline_hierarchy_build_median_ns']:.3f}x | "
            f"{case['current_over_baseline_apply_per_rhs_median_ns']:.3f}x | "
            f"{case['current_over_baseline_solve_per_rhs_median_ns']:.3f}x | "
            f"{case['current_over_baseline_pcg_workspace_bytes']:.3f}x | "
            f"{case['current_over_baseline_peak_rss']:.3f}x |"
        )
    checkpoint = f'''### Cumulative performance refresh — 2026-08-24

- Current SHA: `{current_sha}`; frozen baseline: `{BASELINE_SHA}`.
- All current and baseline solves passed original-system backward-error checks.

| Case | Hierarchy time | CMG apply/RHS | PCG solve/RHS | PCG workspace | Peak RSS |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

- Geometric hierarchy / apply / solve ratios: `{result['geometric_current_over_baseline_hierarchy_build_median_ns']:.3f}x` / `{result['geometric_current_over_baseline_apply_per_rhs_median_ns']:.3f}x` / `{result['geometric_current_over_baseline_solve_per_rhs_median_ns']:.3f}x`.
- Geometric CMG / PCG workspace ratios: `{result['geometric_current_over_baseline_cmg_workspace_bytes']:.3f}x` / `{result['geometric_current_over_baseline_pcg_workspace_bytes']:.3f}x`.
- Evidence: `.ci/performance/cumulative-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Cumulative performance refresh — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Cumulative performance refresh

- Current SHA: `{current_sha}`; baseline: `{BASELINE_SHA}`.
- Geometric hierarchy / apply / solve ratios: `{result['geometric_current_over_baseline_hierarchy_build_median_ns']:.3f}x` / `{result['geometric_current_over_baseline_apply_per_rhs_median_ns']:.3f}x` / `{result['geometric_current_over_baseline_solve_per_rhs_median_ns']:.3f}x`.
- Geometric CMG / PCG workspace ratios: `{result['geometric_current_over_baseline_cmg_workspace_bytes']:.3f}x` / `{result['geometric_current_over_baseline_pcg_workspace_bytes']:.3f}x`.
- Evidence: `.ci/performance/cumulative-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Cumulative performance refresh\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")

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
run(["git", "commit", "-m", "perf: refresh cumulative benchmark evidence"])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push cumulative performance refresh")

if result["status"] != "success":
    raise SystemExit(1)
