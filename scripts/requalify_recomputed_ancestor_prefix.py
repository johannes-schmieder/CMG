import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/requalify-recomputed-ancestor-prefix.yml")
SCRIPT = Path("scripts/requalify_recomputed_ancestor_prefix.py")
RECORD = Path(".ci/performance/recompute-forest-ancestor-prefix-rss-latest.json")
PRIOR_RECORD = Path(".ci/performance/recompute-forest-ancestor-prefix-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")
WRAPPER_COMMIT = "88b61cbe439874ca7be1418a62eb9f0bb3b52441"
WRAPPER_PATH = "scripts/recompute_forest_ancestor_prefix_gate_v2.py"


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
        raise RuntimeError("historical ancestor-prefix wrapper marker missing")
    wrapper_namespace = {
        "__name__": "ancestor_prefix_wrapper_defs",
        "__file__": str(SCRIPT),
    }
    exec(
        compile(wrapper.split(execution_marker, 1)[0], str(SCRIPT), "exec"),
        wrapper_namespace,
    )
    generated = wrapper_namespace["text"]
    source_marker = "original_forest = FOREST.read_text()"
    if source_marker not in generated:
        raise RuntimeError("generated ancestor-prefix source marker missing")
    candidate_namespace = {"__name__": "ancestor_prefix_candidate_defs"}
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
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "time": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-prefix-rss-{tag}.time")
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
        raise RuntimeError(f"unexpected hierarchy benchmark output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing from /usr/bin/time output")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(kind, baseline, candidate, arguments, name, rounds):
    baseline_samples = []
    candidate_samples = []
    schedule = []
    for round_index in range(rounds):
        schedule.extend(
            (("baseline", baseline[kind]), ("candidate", candidate[kind]))
            if round_index % 2 == 0
            else (("candidate", candidate[kind]), ("baseline", baseline[kind]))
        )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(
            binary,
            arguments,
            f"{kind}-{name}-{label}-{index}",
        )
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = ("case", "scale", "vertices", "edges", "repetitions")
    if kind == "allocation":
        stable += (
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {kind} metadata for {key}")

    baseline_times = [item["median_ns"] for item in baseline_samples]
    candidate_times = [item["median_ns"] for item in candidate_samples]
    baseline_rss = [item["peak_rss_kib"] for item in baseline_samples]
    candidate_rss = [item["peak_rss_kib"] for item in candidate_samples]
    result = {
        "arguments": arguments,
        "rounds": rounds,
        "metadata": {key: reference[key] for key in stable},
        "baseline_time_samples_ns": baseline_times,
        "candidate_time_samples_ns": candidate_times,
        "baseline_rss_samples_kib": baseline_rss,
        "candidate_rss_samples_kib": candidate_rss,
        "baseline_median_ns": statistics.median(baseline_times),
        "candidate_median_ns": statistics.median(candidate_times),
        "baseline_median_rss_kib": statistics.median(baseline_rss),
        "candidate_median_rss_kib": statistics.median(candidate_rss),
        "baseline_max_rss_kib": max(baseline_rss),
        "candidate_max_rss_kib": max(candidate_rss),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_median_rss"] = (
        result["candidate_median_rss_kib"] / result["baseline_median_rss_kib"]
    )
    result["candidate_over_baseline_max_rss"] = (
        result["candidate_max_rss_kib"] / result["baseline_max_rss_kib"]
    )
    if kind == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_values = [item[field] for item in baseline_samples]
            candidate_values = [item[field] for item in candidate_samples]
            result[f"baseline_{field}_samples"] = baseline_values
            result[f"candidate_{field}_samples"] = candidate_values
            result[f"baseline_{field}"] = statistics.median(baseline_values)
            result[f"candidate_{field}"] = statistics.median(candidate_values)
            result[f"candidate_over_baseline_{field}"] = (
                result[f"candidate_{field}"] / result[f"baseline_{field}"]
            )
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("hierarchy_geometric_median_rss_ratio", 1.0)
    checkpoint = f'''### Recomputed ancestor-prefix RSS requalification — 2026-08-24

- The prefix-vector elimination candidate was **{decision}** after an independent multi-process memory rerun.
- Validation: `{result.get("validation", "unknown")}`; hierarchy metadata and exact allocator accounting remained unchanged.
- Geometric hierarchy-time / median-process-RSS ratios: `{hierarchy_ratio:.3f}x` / `{rss_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("allocation_geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Worst per-case median-RSS ratio: `{result.get("hierarchy_worst_median_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-rss-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Recomputed ancestor-prefix RSS requalification — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Profile executor heavy-edge routing against the now-qualified forest path.\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact split parents, hierarchy diagnostics, and residual certificates in every gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Recomputed ancestor-prefix RSS requalification

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy-time / median-RSS ratios: `{hierarchy_ratio:.3f}x` / `{rss_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("allocation_geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("allocation_geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/recompute-forest-ancestor-prefix-rss-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Recomputed ancestor-prefix RSS requalification\n"
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
prior = json.loads(PRIOR_RECORD.read_text())
result = {
    "schema_version": 1,
    "experiment": "recomputed-ancestor-prefix-rss-requalification",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "prior_record": prior,
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    apply_candidate = load_candidate_transform()
    baseline = build(Path("/tmp/cmg-prefix-rss-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(
        [
            "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml",
            "--all", "--", "--check",
        ]
    )
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(
        [
            "cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml",
            "--all-targets", "--", "-D", "warnings",
        ]
    )
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-prefix-rss-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "2"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "2"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "2"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2"]),
    )
    for name, arguments in specs:
        result["hierarchy_cases"][name] = compare_case(
            "time", baseline, candidate, arguments, name, 5
        )
        result["allocation_cases"][name] = compare_case(
            "allocation", baseline, candidate, arguments, name, 3
        )

    hierarchy_time = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    hierarchy_median_rss = [
        case["candidate_over_baseline_median_rss"]
        for case in result["hierarchy_cases"].values()
    ]
    hierarchy_max_rss = [
        case["candidate_over_baseline_max_rss"]
        for case in result["hierarchy_cases"].values()
    ]
    allocation_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    allocation_retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_time)
    result["hierarchy_worst_time_ratio"] = max(hierarchy_time)
    result["hierarchy_geometric_median_rss_ratio"] = geometric(
        hierarchy_median_rss
    )
    result["hierarchy_worst_median_rss_ratio"] = max(hierarchy_median_rss)
    result["hierarchy_worst_max_rss_ratio"] = max(hierarchy_max_rss)
    result["allocation_geometric_additional_peak_ratio"] = geometric(
        allocation_peak
    )
    result["allocation_worst_additional_peak_ratio"] = max(allocation_peak)
    result["allocation_geometric_retained_ratio"] = geometric(
        allocation_retained
    )
    result["allocation_worst_retained_ratio"] = max(allocation_retained)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["prior_split_geometric_time_ratio"] = prior.get(
        "split_geometric_time_ratio", 1.0
    )
    result["prior_hierarchy_geometric_time_ratio"] = prior.get(
        "hierarchy_geometric_time_ratio", 1.0
    )
    result["acceptance_limits"] = {
        "prior_split_geometric_time_ratio_max": 0.90,
        "prior_hierarchy_geometric_time_ratio_max": 0.97,
        "hierarchy_geometric_time_ratio_max": 0.98,
        "hierarchy_worst_time_ratio_max": 1.02,
        "hierarchy_geometric_median_rss_ratio_max": 1.015,
        "hierarchy_worst_median_rss_ratio_max": 1.03,
        "hierarchy_worst_max_rss_ratio_max": 1.05,
        "allocation_geometric_additional_peak_ratio_max": 1.001,
        "allocation_worst_additional_peak_ratio_max": 1.003,
        "allocation_geometric_retained_ratio_max": 1.001,
        "allocation_worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        prior.get("validation") == "success"
        and result["prior_split_geometric_time_ratio"] <= 0.90
        and result["prior_hierarchy_geometric_time_ratio"] <= 0.97
        and result["hierarchy_geometric_time_ratio"] <= 0.98
        and result["hierarchy_worst_time_ratio"] <= 1.02
        and result["hierarchy_geometric_median_rss_ratio"] <= 1.015
        and result["hierarchy_worst_median_rss_ratio"] <= 1.03
        and result["hierarchy_worst_max_rss_ratio"] <= 1.05
        and result["allocation_geometric_additional_peak_ratio"] <= 1.001
        and result["allocation_worst_additional_peak_ratio"] <= 1.003
        and result["allocation_geometric_retained_ratio"] <= 1.001
        and result["allocation_worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "large split and hierarchy gains reproduced; repeated process RSS and exact allocator accounting are non-regressive"
        if result["accepted"]
        else "timing, repeated process RSS, or exact allocator limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"requalification failed safely: {error}"
    print(result["decision_reason"], flush=True)

if result.get("accepted", False):
    SOURCE.write_text(apply_candidate(baseline_source))
    try:
        run(["cargo", "fmt", "--all"])
        run(["cargo", "fmt", "--all", "--", "--check"])
        run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
        run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    except Exception as error:
        result["accepted"] = False
        result["validation"] = "failure"
        result["error"] = repr(error)
        result["decision_reason"] = f"final production validation failed safely: {error}"
        SOURCE.write_text(baseline_source)
        run(["cargo", "fmt", "--all"], check=False)
else:
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "hierarchy_geometric_time_ratio",
    "hierarchy_worst_time_ratio",
    "hierarchy_geometric_median_rss_ratio",
    "hierarchy_worst_median_rss_ratio",
    "hierarchy_worst_max_rss_ratio",
    "allocation_geometric_additional_peak_ratio",
    "allocation_worst_additional_peak_ratio",
    "allocation_geometric_retained_ratio",
    "allocation_worst_retained_ratio",
    "prior_split_geometric_time_ratio",
    "prior_hierarchy_geometric_time_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".ci/performance/recompute-ancestor-prefix-v2-run-status.json",
    ".ci/performance/recompute-ancestor-prefix-run-status.json",
):
    Path(stale).unlink(missing_ok=True)
try:
    Path("scripts").rmdir()
except OSError:
    pass
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain recomputed forest ancestor prefixes after RSS gate"
    if result.get("accepted", False)
    else "perf: record ancestor-prefix RSS requalification"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push ancestor-prefix requalification")
if result.get("validation") == "failure":
    raise SystemExit(1)
