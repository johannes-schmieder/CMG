import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/forest-split-gate.rs")
WORKFLOW = Path(".github/workflows/compact-forest-ancestors-two-path.yml")
SCRIPT = Path("scripts/compact_forest_ancestors_two_path_gate.py")
RECORD = Path(".ci/performance/compact-forest-ancestors-two-path-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")
WRAPPER_COMMIT = "297042df2e54283924fba81a9a0dd5e787072477"
WRAPPER_PATH = "scripts/compact_forest_ancestors_gate.py"

BENCHMARK_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::split_forest;

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn parent_vector(case: &str, n: usize) -> Vec<usize> {
    match case {
        "path" => {
            let mut parent = Vec::with_capacity(n);
            for vertex in 0..n {
                parent.push(if vertex + 1 < n { vertex + 1 } else { vertex });
            }
            parent
        }
        "paired-path" => {
            let mut parent = Vec::with_capacity(n);
            for vertex in 0..n {
                let target = if vertex % 8 == 7 || vertex + 1 == n {
                    vertex
                } else {
                    vertex + 1
                };
                parent.push(target);
            }
            parent
        }
        _ => panic!("unknown case"),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap();
    let n = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let parent = parent_vector(&case, n);
    let warmup = split_forest(black_box(&parent)).unwrap();
    let checksum: usize = warmup.iter().take(1_024).copied().sum();
    black_box(warmup);

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let split = split_forest(black_box(&parent)).unwrap();
        elapsed.push(start.elapsed().as_nanos());
        assert_eq!(split.iter().take(1_024).copied().sum::<usize>(), checksum);
        black_box(split);
    }
    println!(
        "{{\"case\":\"{}\",\"vertices\":{},\"repetitions\":{},\"median_ns\":{},\"checksum\":{}}}",
        case,
        n,
        repetitions,
        median(elapsed),
        checksum,
    );
}
'''


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


def load_base_transform():
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
        raise RuntimeError("generated ancestor gate marker missing")
    candidate_namespace = {"__name__": "compact_ancestor_candidate_defs"}
    exec(
        compile(generated.split(source_marker, 1)[0], str(SCRIPT), "exec"),
        candidate_namespace,
    )
    return candidate_namespace["apply_candidate"]


THREE_PATH_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= i32::MAX as usize {
        split_forest_impl_with_storage::<u32, i32>(parent, validate)
    } else if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_storage::<u32, i64>(parent, validate)
    } else {
        split_forest_impl_with_storage::<usize, i64>(parent, validate)
    }
}
'''
TWO_PATH_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= i32::MAX as usize {
        split_forest_impl_with_storage::<u32, i32>(parent, validate)
    } else {
        split_forest_impl_with_storage::<usize, i64>(parent, validate)
    }
}
'''


def apply_candidate(source):
    candidate = load_base_transform()(source)
    if candidate.count(THREE_PATH_ROUTER) != 1:
        raise RuntimeError("three-path ancestor router changed unexpectedly")
    return candidate.replace(THREE_PATH_ROUTER, TWO_PATH_ROUTER, 1)


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        [
            "cargo",
            "build",
            "--release",
            "--bin",
            "forest-split-gate",
        ],
        env=env,
    )
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
    return {
        "split": target / "release" / "forest-split-gate",
        "hierarchy": target / "release" / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-two-path-ancestor-{tag}.time")
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
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        ("case", "vertices", "repetitions", "checksum")
        if kind == "split"
        else (
            "case",
            "scale",
            "vertices",
            "edges",
            "repetitions",
            "levels",
            "hierarchy_matrix_nonzeros",
            "max_post_drop_delta_bytes",
        )
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
    if kind == "hierarchy":
        result["baseline_additional_peak_bytes"] = statistics.median(
            item["median_additional_peak_bytes"] for item in baseline_samples
        )
        result["candidate_additional_peak_bytes"] = statistics.median(
            item["median_additional_peak_bytes"] for item in candidate_samples
        )
        result["baseline_retained_bytes"] = statistics.median(
            item["median_retained_bytes"] for item in baseline_samples
        )
        result["candidate_retained_bytes"] = statistics.median(
            item["median_retained_bytes"] for item in candidate_samples
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
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_hierarchy_peak_rss_ratio", 1.0)
    checkpoint = f'''### Two-path compact forest-ancestor checkpoint — 2026-08-23

- The two-monomorphization `i32`/`i64` ancestor candidate was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric split / hierarchy timing ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst hierarchy process-RSS ratio: `{rss_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/compact-forest-ancestors-two-path-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Two-path compact forest-ancestor checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Two-path compact forest-ancestor gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst hierarchy RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/compact-forest-ancestors-two-path-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Two-path compact forest-ancestor gate\n"
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
TEMP_BENCH.parent.mkdir(parents=True, exist_ok=True)
TEMP_BENCH.write_text(BENCHMARK_SOURCE)
result = {
    "schema_version": 1,
    "experiment": "compact-forest-ancestors-two-path",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-two-path-ancestor-baseline"))
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

    candidate = build(Path("/tmp/cmg-two-path-ancestor-candidate"))
    split_specs = (
        ("path-500k", ["path", "500000", "5"]),
        ("path-1m", ["path", "1000000", "5"]),
        ("paired-path-2m", ["paired-path", "2000000", "5"]),
    )
    hierarchy_specs = (
        ("path-1m", ["path", "1000000", "3"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3"]),
    )
    for name, arguments in split_specs:
        result["split_cases"][name] = compare(
            "split", baseline, candidate, arguments, name
        )
    for name, arguments in hierarchy_specs:
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )

    split_time = [
        case["candidate_over_baseline_time"]
        for case in result["split_cases"].values()
    ]
    hierarchy_time = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    split_rss = [
        case["candidate_over_baseline_peak_rss"]
        for case in result["split_cases"].values()
    ]
    hierarchy_rss = [
        case["candidate_over_baseline_peak_rss"]
        for case in result["hierarchy_cases"].values()
    ]
    additional_peak = [
        case["candidate_over_baseline_additional_peak"]
        for case in result["hierarchy_cases"].values()
    ]
    retained = [
        case["candidate_over_baseline_retained"]
        for case in result["hierarchy_cases"].values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_time)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_time)
    result["path_hierarchy_time_ratio"] = result["hierarchy_cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["dense_hierarchy_time_ratio"] = result["hierarchy_cases"][
        "dense-worker-firm-1.6m"
    ]["candidate_over_baseline_time"]
    result["worst_split_time_ratio"] = max(split_time)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_time)
    result["worst_split_peak_rss_ratio"] = max(split_rss)
    result["worst_hierarchy_peak_rss_ratio"] = max(hierarchy_rss)
    result["geometric_additional_peak_ratio"] = geometric(additional_peak)
    result["worst_additional_peak_ratio"] = max(additional_peak)
    result["geometric_retained_ratio"] = geometric(retained)
    result["worst_retained_ratio"] = max(retained)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["hierarchy_cases"].values()
    )
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.96,
        "hierarchy_geometric_time_ratio_max": 0.99,
        "path_hierarchy_time_ratio_max": 0.99,
        "dense_hierarchy_time_ratio_max": 1.02,
        "worst_split_time_ratio_max": 1.02,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_split_peak_rss_ratio_max": 0.91,
        "worst_hierarchy_peak_rss_ratio_max": 1.03,
        "geometric_additional_peak_ratio_max": 1.0,
        "worst_additional_peak_ratio_max": 1.002,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.96
        and result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["path_hierarchy_time_ratio"] <= 0.99
        and result["dense_hierarchy_time_ratio"] <= 1.02
        and result["worst_split_time_ratio"] <= 1.02
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_split_peak_rss_ratio"] <= 0.91
        and result["worst_hierarchy_peak_rss_ratio"] <= 1.03
        and result["geometric_additional_peak_ratio"] <= 1.0
        and result["worst_additional_peak_ratio"] <= 1.002
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "two-path compact ancestor storage preserved range, removed the extra monomorphization, and passed timing plus exact/process memory gates"
        if result["accepted"]
        else "timing or exact/process memory limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

if not result.get("accepted", False):
    SOURCE.write_text(baseline_source)
    run(["cargo", "fmt", "--all"], check=False)

TEMP_BENCH.unlink(missing_ok=True)
for key in (
    "split_geometric_time_ratio",
    "hierarchy_geometric_time_ratio",
    "path_hierarchy_time_ratio",
    "dense_hierarchy_time_ratio",
    "worst_split_time_ratio",
    "worst_hierarchy_time_ratio",
    "worst_split_peak_rss_ratio",
    "worst_hierarchy_peak_rss_ratio",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
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
    "perf: retain two-path compact forest ancestors"
    if result.get("accepted", False)
    else "perf: record two-path compact forest-ancestor experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push two-path compact ancestor decision")
