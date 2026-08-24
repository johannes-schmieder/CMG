import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
BASELINE_SHA = "b45b252f88925028e3ad9a73a3f75eeab05f6754"
WORKFLOW = Path(".github/workflows/refresh-cumulative-performance-v3.yml")
SCRIPT = Path("scripts/refresh_cumulative_performance_v3.py")
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
    time_path = Path(f"/tmp/cmg-cumulative-v3-{tag}.time")
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
        raise RuntimeError("peak RSS missing from /usr/bin/time output")
    payload = payloads[0]
    payload["process_peak_rss_kib"] = int(rss_match.group(1))
    return payload


def first(payload, *keys):
    for key in keys:
        if key in payload:
            return payload[key]
    raise KeyError(f"none of the expected fields are present: {keys}")


def normalized(payload):
    backward_errors = list(first(payload, "backward_errors"))
    iterations = list(first(payload, "iterations"))
    return {
        "case": first(payload, "case"),
        "vertices": int(first(payload, "vertices")),
        "canonical_edges": int(first(payload, "canonical_edges", "edges")),
        "raw_edges": int(first(payload, "raw_edges")),
        "rhs_count": int(first(payload, "rhs_count")),
        "repetitions": int(first(payload, "repetitions")),
        "graph_build_ns": int(first(payload, "graph_build_median_ns")),
        "hierarchy_build_ns": int(first(payload, "hierarchy_build_median_ns")),
        "apply_ns": int(first(payload, "preconditioner_apply_median_ns")),
        "solve_per_rhs_ns": int(first(payload, "solve_per_rhs_median_ns")),
        "graph_core_bytes": int(first(payload, "graph_core_bytes")),
        "hierarchy_core_bytes": int(first(payload, "hierarchy_core_bytes")),
        "cmg_workspace_bytes": int(
            first(
                payload,
                "cmg_workspace_bytes",
                "cmg_workspace_estimated_bytes",
            )
        ),
        "pcg_workspace_bytes": int(
            first(
                payload,
                "pcg_workspace_bytes",
                "pcg_workspace_estimated_bytes",
            )
        ),
        "terminal_reason": first(payload, "terminal_reason"),
        "level_vertices": list(first(payload, "level_vertices")),
        "level_matrix_nonzeros": list(first(payload, "level_matrix_nonzeros")),
        "iterations": iterations,
        "backward_errors": backward_errors,
        "maximum_backward_error": max(backward_errors, default=0.0),
        "process_peak_rss_kib": int(first(payload, "process_peak_rss_kib")),
    }


def median_field(samples, key):
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
        observation = normalized(
            sample(binary, arguments, f"{name}-{label}-{index}")
        )
        (baseline_samples if label == "baseline" else current_samples).append(
            observation
        )

    stable_fields = (
        "case",
        "vertices",
        "canonical_edges",
        "raw_edges",
        "rhs_count",
        "terminal_reason",
        "level_vertices",
        "level_matrix_nonzeros",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + current_samples:
        for key in stable_fields:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: benchmark metadata changed for {key}")
        if observation["maximum_backward_error"] > 1.0e-8:
            raise RuntimeError(
                f"{name}: uncertified backward error "
                f"{observation['maximum_backward_error']}"
            )

    timing_fields = (
        "graph_build_ns",
        "hierarchy_build_ns",
        "apply_ns",
        "solve_per_rhs_ns",
    )
    memory_fields = (
        "graph_core_bytes",
        "hierarchy_core_bytes",
        "cmg_workspace_bytes",
        "pcg_workspace_bytes",
    )
    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable_fields},
        "baseline_iterations": baseline_samples[-1]["iterations"],
        "current_iterations": current_samples[-1]["iterations"],
        "baseline_maximum_backward_error": max(
            item["maximum_backward_error"] for item in baseline_samples
        ),
        "current_maximum_backward_error": max(
            item["maximum_backward_error"] for item in current_samples
        ),
        # The current cmg-bench also constructs and times CSR after the solve,
        # whereas the frozen benchmark does not. Preserve raw process RSS as
        # context, but do not use it as a comparable memory metric.
        "baseline_process_peak_rss_kib": max(
            item["process_peak_rss_kib"] for item in baseline_samples
        ),
        "current_process_peak_rss_kib": max(
            item["process_peak_rss_kib"] for item in current_samples
        ),
        "process_peak_rss_comparable": False,
    }
    for field in timing_fields + memory_fields:
        baseline_value = median_field(baseline_samples, field)
        current_value = median_field(current_samples, field)
        result[f"baseline_{field}"] = baseline_value
        result[f"current_{field}"] = current_value
        result[f"current_over_baseline_{field}"] = (
            current_value / baseline_value
        )
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    rows = []
    for name, case in result["cases"].items():
        rows.append(
            f"| {name} | "
            f"{case['current_over_baseline_graph_build_ns']:.3f}x | "
            f"{case['current_over_baseline_hierarchy_build_ns']:.3f}x | "
            f"{case['current_over_baseline_apply_ns']:.3f}x | "
            f"{case['current_over_baseline_solve_per_rhs_ns']:.3f}x | "
            f"{case['current_over_baseline_graph_core_bytes']:.3f}x | "
            f"{case['current_over_baseline_pcg_workspace_bytes']:.3f}x |"
        )
    checkpoint = f'''### Robust cumulative performance refresh — 2026-08-24

- Current numerical SHA: `{result['current_numerical_sha']}`; frozen benchmark baseline: `{BASELINE_SHA}`.
- Both binaries were built with the same Rust compiler, release mode, and `target-cpu=native` setting.
- All baseline and current solves passed original-system backward-error checks; hierarchy structure was unchanged.

| Case | Graph build | Hierarchy build | CMG apply | PCG solve/RHS | Graph core bytes | PCG workspace |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

- Geometric graph / hierarchy / apply / solve ratios: `{result['geometric_current_over_baseline_graph_build_ns']:.3f}x` / `{result['geometric_current_over_baseline_hierarchy_build_ns']:.3f}x` / `{result['geometric_current_over_baseline_apply_ns']:.3f}x` / `{result['geometric_current_over_baseline_solve_per_rhs_ns']:.3f}x`.
- Geometric graph-core / hierarchy-core / CMG-workspace / PCG-workspace ratios: `{result['geometric_current_over_baseline_graph_core_bytes']:.3f}x` / `{result['geometric_current_over_baseline_hierarchy_core_bytes']:.3f}x` / `{result['geometric_current_over_baseline_cmg_workspace_bytes']:.3f}x` / `{result['geometric_current_over_baseline_pcg_workspace_bytes']:.3f}x`.
- Raw process RSS was recorded but is not compared because the current benchmark performs an additional CSR qualification after solving.
- Evidence: `.ci/performance/cumulative-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Robust cumulative performance refresh — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Robust cumulative performance refresh

- Current numerical SHA: `{result['current_numerical_sha']}`; baseline: `{BASELINE_SHA}`.
- Geometric graph / hierarchy / apply / solve ratios: `{result['geometric_current_over_baseline_graph_build_ns']:.3f}x` / `{result['geometric_current_over_baseline_hierarchy_build_ns']:.3f}x` / `{result['geometric_current_over_baseline_apply_ns']:.3f}x` / `{result['geometric_current_over_baseline_solve_per_rhs_ns']:.3f}x`.
- Geometric graph-core / hierarchy-core / CMG-workspace / PCG-workspace ratios: `{result['geometric_current_over_baseline_graph_core_bytes']:.3f}x` / `{result['geometric_current_over_baseline_hierarchy_core_bytes']:.3f}x` / `{result['geometric_current_over_baseline_cmg_workspace_bytes']:.3f}x` / `{result['geometric_current_over_baseline_pcg_workspace_bytes']:.3f}x`.
- Evidence: `.ci/performance/cumulative-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Robust cumulative performance refresh\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


staging_sha = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()
# CI bookkeeping and one-shot workflow commits do not alter numerical source.
# Record the latest tested numerical checkpoint separately when available.
latest_ci = json.loads(Path(".ci/latest.json").read_text())
current_numerical_sha = latest_ci.get("tested_sha", staging_sha)
baseline_root = Path("/tmp/cmg-cumulative-v3-baseline")
run(["git", "worktree", "remove", "--force", baseline_root], check=False)
run(["git", "worktree", "add", "--detach", baseline_root, BASELINE_SHA])

result = {
    "schema_version": 2,
    "benchmark": "robust-cumulative-current-versus-frozen-baseline",
    "baseline_sha": BASELINE_SHA,
    "staging_sha": staging_sha,
    "current_numerical_sha": current_numerical_sha,
    "status": "not_run",
    "process_peak_rss_comparable": False,
    "cases": {},
}
try:
    baseline_binary = build(
        baseline_root,
        Path("/tmp/cmg-cumulative-v3-baseline-target"),
    )
    current_binary = build(
        ROOT,
        Path("/tmp/cmg-cumulative-v3-current-target"),
    )
    specs = (
        (
            "path-100k",
            [
                "--case",
                "path",
                "--vertices",
                "100000",
                "--rhs",
                "8",
                "--repetitions",
                "5",
            ],
        ),
        (
            "worker-firm-100k",
            [
                "--case",
                "worker-firm",
                "--vertices",
                "100000",
                "--rhs",
                "8",
                "--repetitions",
                "5",
            ],
        ),
        (
            "path-500k",
            [
                "--case",
                "path",
                "--vertices",
                "500000",
                "--rhs",
                "8",
                "--repetitions",
                "3",
            ],
        ),
        (
            "worker-firm-500k",
            [
                "--case",
                "worker-firm",
                "--vertices",
                "500000",
                "--rhs",
                "8",
                "--repetitions",
                "3",
            ],
        ),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline_binary,
            current_binary,
            arguments,
            name,
        )

    ratio_fields = (
        "graph_build_ns",
        "hierarchy_build_ns",
        "apply_ns",
        "solve_per_rhs_ns",
        "graph_core_bytes",
        "hierarchy_core_bytes",
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
    result["status"] = "success"
    update_documents(result)
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    print(f"cumulative refresh failed safely: {error}", flush=True)
finally:
    run(["git", "worktree", "remove", "--force", baseline_root], check=False)

RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

for stale in (
    ".github/workflows/refresh-cumulative-performance.yml",
    ".github/workflows/refresh-cumulative-performance-v2.yml",
    ".github/workflows/refresh-cumulative-performance-v3.yml",
    "scripts/refresh_cumulative_performance.py",
    "scripts/refresh_cumulative_performance_v2.py",
    "scripts/refresh_cumulative_performance_v3.py",
):
    Path(stale).unlink(missing_ok=True)

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
run(["git", "commit", "-m", "perf: record robust cumulative benchmark evidence"])
for _ in range(12):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push robust cumulative performance refresh")

if result["status"] != "success":
    raise SystemExit(1)
