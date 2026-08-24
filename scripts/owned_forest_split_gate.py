import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/owned-forest-split.yml")
SCRIPT = Path("scripts/owned_forest_split_gate.py")
RECORD = Path(".ci/performance/owned-forest-split-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")


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
    time_path = Path(f"/tmp/cmg-owned-split-{tag}.time")
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
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
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
                raise RuntimeError(f"{name}: hierarchy changed {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": statistics.median(
            item["median_ns"] for item in baseline_samples
        ),
        "candidate_median_ns": statistics.median(
            item["median_ns"] for item in candidate_samples
        ),
        "baseline_median_rss_kib": statistics.median(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_median_rss_kib": statistics.median(
            item["peak_rss_kib"] for item in candidate_samples
        ),
        "baseline_max_rss_kib": max(
            item["peak_rss_kib"] for item in baseline_samples
        ),
        "candidate_max_rss_kib": max(
            item["peak_rss_kib"] for item in candidate_samples
        ),
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
            baseline_value = statistics.median(
                item[field] for item in baseline_samples
            )
            candidate_value = statistics.median(
                item[field] for item in candidate_samples
            )
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = (
                candidate_value / baseline_value
            )
    return result


OLD_INTERNAL_USE = '''    let mut final_parent = split_forest_trusted(&heavy_parent)?;
    drop(heavy_parent);
'''
NEW_INTERNAL_USE = '''    let mut final_parent = split_forest_owned_trusted(heavy_parent)?;
'''
OLD_TRUSTED = '''fn split_forest_trusted(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, false)
}
'''
NEW_TRUSTED = '''fn split_forest_trusted(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_impl(parent, false)
}

fn split_forest_owned_trusted(parent: Vec<usize>) -> Result<Vec<usize>, CmgError> {
    split_forest_owned_impl(parent)
}
'''
OLD_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_indegree::<u32>(parent, validate)
    } else {
        split_forest_impl_with_indegree::<usize>(parent, validate)
    }
}

fn split_forest_impl_with_indegree<I: ForestIndegree>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
    if validate {
        validate_parent(parent)?;
    }
    let n = parent.len();
    let mut forest = parent.to_vec();
'''
NEW_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if validate {
        validate_parent(parent)?;
    }
    split_forest_owned_impl(parent.to_vec())
}

fn split_forest_owned_impl(parent: Vec<usize>) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_owned_impl_with_indegree::<u32>(parent)
    } else {
        split_forest_owned_impl_with_indegree::<usize>(parent)
    }
}

fn split_forest_owned_impl_with_indegree<I: ForestIndegree>(
    mut forest: Vec<usize>,
) -> Result<Vec<usize>, CmgError> {
    let n = forest.len();
'''
TEST_MODULE = '''

#[cfg(test)]
mod owned_forest_split_tests {
    use super::{split_forest_owned_trusted, split_forest_trusted};

    #[test]
    fn owned_and_borrowed_trusted_split_paths_match() {
        let parent = vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11];
        assert_eq!(
            split_forest_owned_trusted(parent.clone()).unwrap(),
            split_forest_trusted(&parent).unwrap(),
        );
    }
}
'''


def apply_candidate(source):
    candidate = source
    replacements = (
        (OLD_INTERNAL_USE, NEW_INTERNAL_USE, "lean grouping split call"),
        (OLD_TRUSTED, NEW_TRUSTED, "trusted split wrapper"),
        (OLD_ROUTER, NEW_ROUTER, "owned split router"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod owned_forest_split_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    time_ratio = result.get("time_geometric_ratio", 1.0)
    peak_ratio = result.get("allocation_geometric_additional_peak_ratio", 1.0)
    checkpoint = f'''### Owned forest-split checkpoint — 2026-08-24

- Consuming the hierarchy-owned heavy-parent vector in the trusted splitter was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; hierarchy diagnostics were identical.
- Geometric hierarchy-time / exact additional-peak ratios: `{time_ratio:.3f}x` / `{peak_ratio:.3f}x`.
- Worst median-process-RSS / retained-hierarchy ratios: `{result.get("worst_median_rss_ratio", 1.0):.3f}x` / `{result.get("allocation_worst_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/owned-forest-split-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Owned forest-split checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile hierarchy setup after the latest retained forest changes.\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact hierarchy and residual certificates in every further gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Owned forest-split gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy-time / exact additional-peak ratios: `{time_ratio:.3f}x` / `{peak_ratio:.3f}x`.
- Evidence: `.ci/performance/owned-forest-split-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Owned forest-split gate\n"
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
result = {
    "schema_version": 1,
    "experiment": "owned-trusted-forest-split",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "time_cases": {},
    "allocation_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-owned-split-baseline"))
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
    candidate = build(Path("/tmp/cmg-owned-split-candidate"))
    result["validation"] = "success"

    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "3"]),
    )
    for name, arguments in specs:
        result["time_cases"][name] = compare_case(
            "time", baseline, candidate, arguments, name
        )
        result["allocation_cases"][name] = compare_case(
            "allocation", baseline, candidate, arguments, name
        )

    time_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["time_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_median_rss"]
        for collection in (result["time_cases"], result["allocation_cases"])
        for case in collection.values()
    ]
    allocation_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    allocation_retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    result["time_geometric_ratio"] = geometric(time_ratios)
    result["worst_time_ratio"] = max(time_ratios)
    result["worst_median_rss_ratio"] = max(rss_ratios)
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
    result["acceptance_limits"] = {
        "time_geometric_ratio_max": 0.98,
        "worst_time_ratio_max": 1.025,
        "worst_median_rss_ratio_max": 1.01,
        "allocation_geometric_additional_peak_ratio_max": 0.94,
        "allocation_worst_additional_peak_ratio_max": 0.985,
        "allocation_geometric_retained_ratio_max": 1.001,
        "allocation_worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["time_geometric_ratio"] <= 0.98
        and result["worst_time_ratio"] <= 1.025
        and result["worst_median_rss_ratio"] <= 1.01
        and result["allocation_geometric_additional_peak_ratio"] <= 0.94
        and result["allocation_worst_additional_peak_ratio"] <= 0.985
        and result["allocation_geometric_retained_ratio"] <= 1.001
        and result["allocation_worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; hierarchy setup consumes the owned parent vector and removes one full native-width clone from peak memory"
        if result["accepted"]
        else "correctness passed, but hierarchy timing or exact/process memory limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if result.get("accepted", False):
    SOURCE.write_text(apply_candidate(baseline_source))
    try:
        run(["cargo", "fmt", "--all"])
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
    "time_geometric_ratio", "worst_time_ratio", "worst_median_rss_ratio",
    "allocation_geometric_additional_peak_ratio",
    "allocation_worst_additional_peak_ratio",
    "allocation_geometric_retained_ratio",
    "allocation_worst_retained_ratio",
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
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain owned forest splitting"
    if result.get("accepted", False)
    else "perf: record owned forest-split experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push owned forest-split decision")
if result.get("validation") == "failure":
    raise SystemExit(1)
