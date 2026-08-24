import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
WORKFLOW = Path(".github/workflows/rootless-forest-labels.yml")
SCRIPT = Path("scripts/rootless_forest_label_gate.py")
RECORD = Path(".ci/performance/rootless-forest-labels-latest.json")
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
    time_path = Path(f"/tmp/cmg-rootless-label-{tag}.time")
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
    rss_line = next(
        line
        for line in time_path.read_text().splitlines()
        if "Maximum resident set size (kbytes):" in line
    )
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_line.rsplit(":", 1)[1].strip())
    return payload


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
        )
    ):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
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
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
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


OLD_FUNCTION = '''fn forest_component_labels_trusted(parent: &[usize]) -> (Vec<usize>, usize) {
    let n = parent.len();
    let mut disjoint_set: Vec<usize> = (0..n).collect();
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut disjoint_set, vertex, target);
    }
    for vertex in 0..n {
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex);
    }

    let mut root_to_label = vec![usize::MAX; n];
    let mut labels = vec![0; n];
    let mut aggregate_count = 0usize;
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        let label = if root_to_label[root] == usize::MAX {
            let next = aggregate_count;
            aggregate_count += 1;
            root_to_label[root] = next;
            next
        } else {
            root_to_label[root]
        };
        labels[vertex] = label;
    }
    (labels, aggregate_count)
}
'''

NEW_FUNCTION = '''fn forest_component_labels_trusted(parent: &[usize]) -> (Vec<usize>, usize) {
    let n = parent.len();
    let mut disjoint_set: Vec<usize> = (0..n).collect();
    for (vertex, &target) in parent.iter().enumerate() {
        union_min_root(&mut disjoint_set, vertex, target);
    }
    for vertex in 0..n {
        disjoint_set[vertex] = find_root(&mut disjoint_set, vertex);
    }

    // `union_min_root` makes every component root its smallest vertex. During
    // the ascending scan, a non-root vertex therefore finds its component
    // label already stored at `labels[root]`; no separate root-to-label vector
    // is needed.
    let mut labels = vec![0; n];
    let mut aggregate_count = 0usize;
    for (vertex, &root) in disjoint_set.iter().enumerate() {
        let label = if root == vertex {
            let next = aggregate_count;
            aggregate_count += 1;
            next
        } else {
            debug_assert!(root < vertex);
            labels[root]
        };
        labels[vertex] = label;
    }
    (labels, aggregate_count)
}
'''

TEST_MODULE = '''

#[cfg(test)]
mod rootless_forest_label_tests {
    use super::{forest_component_labels, forest_component_labels_trusted};

    fn reference(parent: &[usize]) -> (Vec<usize>, usize) {
        let (checked_labels, sizes) = super::forest_components(parent).unwrap();
        (checked_labels, sizes.len())
    }

    #[test]
    fn rootless_labels_match_reference_on_mixed_components() {
        let parent = vec![1, 1, 3, 3, 4, 6, 6, 8, 8, 9, 11, 11];
        let expected = reference(&parent);
        assert_eq!(forest_component_labels_trusted(&parent), expected);
        assert_eq!(forest_component_labels(&parent).unwrap(), expected);
    }

    #[test]
    fn rootless_labels_preserve_root_ordering() {
        let parent = vec![0, 2, 2, 4, 4, 5, 7, 7];
        let (labels, count) = forest_component_labels_trusted(&parent);
        assert_eq!(count, 5);
        assert_eq!(labels, vec![0, 1, 1, 2, 2, 3, 4, 4]);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_FUNCTION) != 1:
        raise RuntimeError("trusted forest-label function changed unexpectedly")
    candidate = source.replace(OLD_FUNCTION, NEW_FUNCTION, 1)
    if "mod rootless_forest_label_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    time_ratio = result.get("time_geometric_ratio", 1.0)
    peak_ratio = result.get("geometric_additional_peak_ratio", 1.0)
    retained_ratio = result.get("geometric_retained_ratio", 1.0)
    checkpoint = f'''### Rootless forest-label checkpoint — 2026-08-24

- Reusing `labels[root]` instead of a full root-to-label vector was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy-build ratio: `{time_ratio:.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/rootless-forest-labels-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Rootless forest-label checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Rootless forest-label gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy-build ratio: `{time_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{peak_ratio:.3f}x` / `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/rootless-forest-labels-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Rootless forest-label gate\n"
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
    "experiment": "rootless-forest-labels",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "time_cases": {},
    "allocation_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-rootless-label-baseline"))
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

    candidate = build(Path("/tmp/cmg-rootless-label-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "3"]),
    )
    for name, arguments in specs:
        result["time_cases"][name] = compare(
            "time", baseline, candidate, arguments, name
        )
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    time_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["time_cases"].values()
    ]
    peak_ratios = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["time_cases"], result["allocation_cases"])
        for case in collection.values()
    ]
    result["time_geometric_ratio"] = geometric(time_ratios)
    result["worst_time_ratio"] = max(time_ratios)
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["acceptance_limits"] = {
        "time_geometric_ratio_max": 1.005,
        "worst_time_ratio_max": 1.035,
        "geometric_additional_peak_ratio_max": 0.97,
        "worst_additional_peak_ratio_max": 1.001,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "worst_peak_rss_ratio_max": 1.03,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["time_geometric_ratio"] <= 1.005
        and result["worst_time_ratio"] <= 1.035
        and result["geometric_additional_peak_ratio"] <= 0.97
        and result["worst_additional_peak_ratio"] <= 1.001
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["worst_peak_rss_ratio"] <= 1.03
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; the redundant root-to-label vector was removed with materially lower exact peak allocation"
        if result["accepted"]
        else "qualification passed, but timing or exact/process memory limits were not all met"
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
    "time_geometric_ratio",
    "worst_time_ratio",
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
    "perf: retain rootless forest labels"
    if result.get("accepted", False)
    else "perf: record rootless forest-label experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push rootless forest-label decision")
