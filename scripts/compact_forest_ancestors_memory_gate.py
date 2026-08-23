import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/compact-forest-ancestors-memory.yml")
SCRIPT = Path("scripts/compact_forest_ancestors_memory_gate.py")
RECORD = Path(".ci/performance/compact-forest-ancestors-memory-latest.json")
TIMING_RECORD = Path(".ci/performance/compact-forest-ancestors-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")
WRAPPER_COMMIT = "297042df2e54283924fba81a9a0dd5e787072477"
WRAPPER_PATH = "scripts/compact_forest_ancestors_gate.py"


def run(command, *, env=None, timeout=7200, check=True):
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        env=env,
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


def load_candidate_transform():
    wrapper = subprocess.check_output(
        ["git", "show", f"{WRAPPER_COMMIT}:{WRAPPER_PATH}"],
        cwd=ROOT,
        text=True,
    )
    execution_marker = 'compile(text, str(Path(__file__)), "exec")'
    if execution_marker not in wrapper:
        raise RuntimeError("ancestor wrapper execution marker missing")
    wrapper_namespace = {
        "__name__": "compact_ancestor_wrapper_defs",
        "__file__": str(SCRIPT),
    }
    exec(
        compile(wrapper.split(execution_marker, 1)[0], str(SCRIPT), "exec"),
        wrapper_namespace,
    )
    generated = wrapper_namespace["text"]
    source_marker = "baseline_source = SOURCE.read_text()"
    if source_marker not in generated:
        raise RuntimeError("generated ancestor gate source marker missing")
    candidate_namespace = {"__name__": "compact_ancestor_candidate_defs"}
    exec(
        compile(generated.split(source_marker, 1)[0], str(SCRIPT), "exec"),
        candidate_namespace,
    )
    return candidate_namespace["apply_candidate"]


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
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
    return target / "release" / "hierarchy-alloc"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-compact-ancestor-memory-{tag}.time")
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
        raise RuntimeError(f"unexpected hierarchy allocation output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        "case",
        "scale",
        "vertices",
        "edges",
        "repetitions",
        "levels",
        "hierarchy_matrix_nonzeros",
        "max_post_drop_delta_bytes",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable allocation metadata for {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": statistics.median(
            item["median_ns"] for item in baseline_samples
        ),
        "candidate_median_ns": statistics.median(
            item["median_ns"] for item in candidate_samples
        ),
        "baseline_peak_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_peak_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
        "baseline_additional_peak_bytes": statistics.median(
            item["median_additional_peak_bytes"] for item in baseline_samples
        ),
        "candidate_additional_peak_bytes": statistics.median(
            item["median_additional_peak_bytes"] for item in candidate_samples
        ),
        "baseline_retained_bytes": statistics.median(
            item["median_retained_bytes"] for item in baseline_samples
        ),
        "candidate_retained_bytes": statistics.median(
            item["median_retained_bytes"] for item in candidate_samples
        ),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    result["candidate_over_baseline_additional_peak"] = (
        result["candidate_additional_peak_bytes"]
        / result["baseline_additional_peak_bytes"]
    )
    result["candidate_over_baseline_retained"] = (
        result["candidate_retained_bytes"] / result["baseline_retained_bytes"]
    )
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    prior_split = result.get("prior_split_geometric_time_ratio", 1.0)
    rerun_hierarchy = result.get("rerun_hierarchy_geometric_time_ratio", 1.0)
    peak_ratio = result.get("geometric_additional_peak_ratio", 1.0)
    retained_ratio = result.get("geometric_retained_ratio", 1.0)
    checkpoint = f'''### Compact forest-ancestor exact-memory checkpoint — 2026-08-23

- The compact `i32` ancestor-count candidate was **{decision}** after exact-memory requalification.
- Validation: `{result.get("validation", "unknown")}`.
- Prior direct-split / rerun hierarchy timing ratios: `{prior_split:.3f}x` / `{rerun_hierarchy:.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio in the memory rerun: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/compact-forest-ancestors-memory-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Compact forest-ancestor exact-memory checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Compact forest-ancestor exact-memory gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Prior split / rerun hierarchy ratios: `{prior_split:.3f}x` / `{rerun_hierarchy:.3f}x`.
- Exact additional-peak / retained ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Evidence: `.ci/performance/compact-forest-ancestors-memory-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Compact forest-ancestor exact-memory gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


baseline_source = SOURCE.read_text()
timing = json.loads(TIMING_RECORD.read_text())
result = {
    "schema_version": 1,
    "experiment": "compact-forest-ancestor-exact-memory-requalification",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
    "prior_timing_record": timing,
}

try:
    apply_candidate = load_candidate_transform()
    baseline = build(Path("/tmp/cmg-compact-ancestor-memory-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
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
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
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
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-compact-ancestor-memory-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline, candidate, arguments, name
        )

    peak_ratios = [
        case["candidate_over_baseline_additional_peak"]
        for case in result["cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_retained"]
        for case in result["cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for case in result["cases"].values()
    ]
    rerun_time_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["cases"].values()
    ]
    split_ratios = [
        case["candidate_over_baseline_time"]
        for case in timing.get("split_cases", {}).values()
    ]
    split_rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for case in timing.get("split_cases", {}).values()
    ]
    result["prior_split_geometric_time_ratio"] = geometric(split_ratios)
    result["prior_worst_split_peak_rss_ratio"] = max(split_rss_ratios)
    result["prior_hierarchy_geometric_time_ratio"] = geometric(
        [
            case["candidate_over_baseline_time"]
            for case in timing.get("hierarchy_cases", {}).values()
        ]
    )
    result["rerun_hierarchy_geometric_time_ratio"] = geometric(
        rerun_time_ratios
    )
    result["rerun_path_hierarchy_time_ratio"] = result["cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["rerun_dense_hierarchy_time_ratio"] = result["cases"][
        "dense-worker-firm-1.6m"
    ]["candidate_over_baseline_time"]
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["cases"].values()
    )
    result["acceptance_limits"] = {
        "prior_split_geometric_time_ratio_max": 0.95,
        "prior_worst_split_peak_rss_ratio_max": 0.90,
        "prior_hierarchy_geometric_time_ratio_max": 0.99,
        "rerun_hierarchy_geometric_time_ratio_max": 0.99,
        "rerun_path_hierarchy_time_ratio_max": 0.99,
        "rerun_dense_hierarchy_time_ratio_max": 1.02,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "worst_peak_rss_ratio_max": 1.03,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        len(split_ratios) == 3
        and result["prior_split_geometric_time_ratio"] <= 0.95
        and result["prior_worst_split_peak_rss_ratio"] <= 0.90
        and result["prior_hierarchy_geometric_time_ratio"] <= 0.99
        and result["rerun_hierarchy_geometric_time_ratio"] <= 0.99
        and result["rerun_path_hierarchy_time_ratio"] <= 0.99
        and result["rerun_dense_hierarchy_time_ratio"] <= 1.02
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["worst_peak_rss_ratio"] <= 1.03
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "timing gains reproduced with non-regressive exact hierarchy memory; compact i32 ancestor counts retained"
        if result["accepted"]
        else "timing or exact/process memory requalification limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "prior_split_geometric_time_ratio",
    "prior_worst_split_peak_rss_ratio",
    "prior_hierarchy_geometric_time_ratio",
    "rerun_hierarchy_geometric_time_ratio",
    "rerun_path_hierarchy_time_ratio",
    "rerun_dense_hierarchy_time_ratio",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

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
message = (
    "perf: retain compact forest ancestor counts after exact memory gate"
    if result.get("accepted", False)
    else "perf: record compact forest-ancestor exact-memory result"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push compact forest-ancestor memory decision")
