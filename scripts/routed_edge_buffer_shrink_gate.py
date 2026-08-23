import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
WORKFLOW = Path(".github/workflows/routed-edge-buffer-shrink.yml")
SCRIPT = Path("scripts/routed_edge_buffer_shrink_gate.py")
RECORD = Path(".ci/performance/routed-edge-buffer-shrink-latest.json")
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
            "graph-build",
            "--bin",
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "graph": release / "graph-build",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-routed-shrink-{tag}.time")
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


def compare(kind, baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
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

    stable = ("case", "scale", "vertices", "repetitions")
    if kind == "graph":
        stable += ("raw_edges", "retained_edges")
    elif kind == "hierarchy":
        stable += ("edges",)
    else:
        stable += (
            "edges",
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable {kind} metadata for {key}")

    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    result = {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable},
    }
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


OLD_SHRINK = '''        // Filtered coarse-edge iterators have a zero lower size hint, so their
        // vectors may grow beyond the final length. Do not retain that spare
        // capacity in every hierarchy level.
        if raw.capacity() != raw.len() {
            raw.shrink_to_fit();
        }
'''
NEW_SHRINK = '''        // Filtered coarse-edge iterators can retain substantial spare capacity.
        // Compact when the saving is both material in bytes and large relative
        // to the retained edge set; avoid copying dense levels merely to release
        // a small fraction of their storage.
        if should_shrink_edge_buffer(raw.len(), raw.capacity()) {
            raw.shrink_to_fit();
        }
'''
HELPER_MARKER = '''fn endpoint_key(edge: &Edge) -> u64 {
'''
HELPER = '''const EDGE_BUFFER_SHRINK_MIN_SAVINGS_BYTES: usize = 1 << 20;
const EDGE_BUFFER_SHRINK_MIN_SPARE_DENOMINATOR: usize = 8;

fn should_shrink_edge_buffer(len: usize, capacity: usize) -> bool {
    let spare = capacity.saturating_sub(len);
    let spare_bytes = spare.saturating_mul(std::mem::size_of::<Edge>());
    let minimum_relative_spare = len.div_ceil(EDGE_BUFFER_SHRINK_MIN_SPARE_DENOMINATOR);
    spare_bytes >= EDGE_BUFFER_SHRINK_MIN_SAVINGS_BYTES
        && spare >= minimum_relative_spare
}

'''
TEST_MODULE = '''

#[cfg(test)]
mod routed_edge_buffer_shrink_tests {
    use super::should_shrink_edge_buffer;

    #[test]
    fn shrink_router_requires_material_absolute_and_relative_savings() {
        assert!(should_shrink_edge_buffer(500_000, 1_000_000));
        assert!(!should_shrink_edge_buffer(900_000, 1_000_000));
        assert!(!should_shrink_edge_buffer(10_000, 20_000));
        assert!(!should_shrink_edge_buffer(1_000_000, 1_000_000));
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_SHRINK) != 1:
        raise RuntimeError("edge-buffer shrink site changed unexpectedly")
    if source.count(HELPER_MARKER) != 1:
        raise RuntimeError("endpoint-key helper marker changed unexpectedly")
    candidate = source.replace(OLD_SHRINK, NEW_SHRINK, 1)
    candidate = candidate.replace(HELPER_MARKER, HELPER + HELPER_MARKER, 1)
    if "mod routed_edge_buffer_shrink_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    retained_ratio = result.get("allocation_geometric_retained_ratio", 1.0)
    peak_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Routed edge-buffer shrink checkpoint — 2026-08-23

- Skipping low-value coarse-edge `shrink_to_fit` copies was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric hierarchy-build ratio: `{hierarchy_ratio:.3f}x`.
- Geometric exact retained-hierarchy ratio: `{retained_ratio:.3f}x`.
- Worst process peak-RSS ratio: `{peak_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/routed-edge-buffer-shrink-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Routed edge-buffer shrink checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Routed edge-buffer shrink gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Hierarchy-build ratio: `{hierarchy_ratio:.3f}x`.
- Exact retained-hierarchy ratio: `{retained_ratio:.3f}x`.
- Worst peak-RSS ratio: `{peak_ratio:.3f}x`.
- Evidence: `.ci/performance/routed-edge-buffer-shrink-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Routed edge-buffer shrink gate\n"
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
    "experiment": "routed-edge-buffer-shrink",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "graph_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    baseline = build(Path("/tmp/cmg-routed-shrink-baseline"))
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

    candidate = build(Path("/tmp/cmg-routed-shrink-candidate"))
    graph_specs = (
        ("unique-1m", ["unique", "1000000", "2"]),
        ("duplicates-16-1.6m", ["duplicates-16", "100000", "2"]),
        ("coarse-collisions-1.6m", ["coarse-collisions", "100000", "2"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "2"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "2"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "2"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "2"]),
    )
    for name, arguments in graph_specs:
        result["graph_cases"][name] = compare(
            "graph", baseline, candidate, arguments, name
        )
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    graph_time = [
        case["candidate_over_baseline_time"]
        for case in result["graph_cases"].values()
    ]
    hierarchy_time = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    added_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    rss = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (
            result["graph_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
    result["graph_geometric_time_ratio"] = geometric(graph_time)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_time)
    result["allocation_geometric_retained_ratio"] = geometric(retained)
    result["allocation_worst_retained_ratio"] = max(retained)
    result["allocation_geometric_additional_peak_ratio"] = geometric(added_peak)
    result["worst_time_ratio"] = max(graph_time + hierarchy_time)
    result["worst_peak_rss_ratio"] = max(rss)
    dense_ratios = [
        result["hierarchy_cases"][name]["candidate_over_baseline_time"]
        for name in ("dense-worker-firm-1.6m", "dense-worker-firm-3.2m")
    ]
    result["dense_hierarchy_geometric_time_ratio"] = geometric(dense_ratios)
    result["acceptance_limits"] = {
        "hierarchy_geometric_time_ratio_max": 0.99,
        "dense_hierarchy_geometric_time_ratio_max": 0.97,
        "graph_geometric_time_ratio_max": 1.01,
        "worst_time_ratio_max": 1.05,
        "allocation_geometric_retained_ratio_max": 1.04,
        "allocation_worst_retained_ratio_max": 1.08,
        "allocation_geometric_additional_peak_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.05,
    }
    result["accepted"] = (
        result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["dense_hierarchy_geometric_time_ratio"] <= 0.97
        and result["graph_geometric_time_ratio"] <= 1.01
        and result["worst_time_ratio"] <= 1.05
        and result["allocation_geometric_retained_ratio"] <= 1.04
        and result["allocation_worst_retained_ratio"] <= 1.08
        and result["allocation_geometric_additional_peak_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.05
    )
    result["decision_reason"] = (
        "full qualification passed; low-value edge-buffer copies were avoided with material dense/setup gains inside a bounded memory budget"
        if result["accepted"]
        else "qualification passed, but setup gains or retained/peak memory limits were not all met"
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
    "graph_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "dense_hierarchy_geometric_time_ratio",
    "allocation_geometric_retained_ratio",
    "allocation_worst_retained_ratio",
    "allocation_geometric_additional_peak_ratio",
    "worst_time_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
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
    "perf: retain routed edge-buffer shrink"
    if result.get("accepted", False)
    else "perf: record routed edge-buffer shrink experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push routed edge-buffer shrink decision")
