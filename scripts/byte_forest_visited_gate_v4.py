import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/forest-visited-gate.rs")
WORKFLOW = Path(".github/workflows/byte-forest-visited-v4.yml")
SCRIPT = Path("scripts/byte_forest_visited_gate_v4.py")
RECORD = Path(".ci/performance/byte-forest-visited-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCHMARK_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::split_forest;

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn parent_vector(case: &str, n: usize) -> Vec<usize> {
    match case {
        "path" => (0..n)
            .map(|vertex| if vertex + 1 < n { vertex + 1 } else { vertex })
            .collect(),
        "segmented-path" => (0..n)
            .map(|vertex| {
                if vertex % 32 == 31 || vertex + 1 == n {
                    vertex
                } else {
                    vertex + 1
                }
            })
            .collect(),
        "paired-path" => (0..n)
            .map(|vertex| {
                if vertex % 8 == 7 || vertex + 1 == n {
                    vertex
                } else {
                    vertex + 1
                }
            })
            .collect(),
        _ => panic!("unknown case"),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().expect("case");
    let vertices = arguments
        .next()
        .expect("vertices")
        .parse::<usize>()
        .expect("valid vertices");
    let repetitions = arguments
        .next()
        .expect("repetitions")
        .parse::<usize>()
        .expect("valid repetitions")
        .max(1);
    let parent = parent_vector(&case, vertices);
    let reference = split_forest(black_box(&parent)).expect("warmup split");
    let checksum = reference
        .iter()
        .enumerate()
        .fold(0_u64, |state, (index, &value)| {
            state.wrapping_add((index as u64 + 1).wrapping_mul(value as u64 + 3))
        });
    black_box(reference);

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let started = Instant::now();
        let split = split_forest(black_box(&parent)).expect("split");
        elapsed.push(started.elapsed().as_nanos());
        let observed = split
            .iter()
            .enumerate()
            .fold(0_u64, |state, (index, &value)| {
                state.wrapping_add((index as u64 + 1).wrapping_mul(value as u64 + 3))
            });
        assert_eq!(observed, checksum);
        black_box(split);
    }

    println!(
        "{{\"case\":\"{}\",\"vertices\":{},\"repetitions\":{},\"median_ns\":{},\"checksum\":{}}}",
        case,
        vertices,
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


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(
        ["cargo", "build", "--release", "--bin", "forest-visited-gate"],
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
            "hierarchy-build",
            "--bin",
            "hierarchy-alloc",
        ],
        env=env,
    )
    release = target / "release"
    return {
        "split": release / "forest-visited-gate",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-byte-visited-{tag}.time")
    completed = run(
        ["/usr/bin/time", "-v", "-o", time_path, binary, *arguments]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output: {payloads}")
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

    stable = (
        ("case", "vertices", "repetitions", "checksum")
        if kind == "split"
        else ("case", "scale", "vertices", "edges", "repetitions")
    )
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


OLD_VISITED = "    let mut visited = vec![false; n];\n"
NEW_VISITED = "    let mut visited = vec![0_u8; n];\n"
OLD_OUTER = (
    "        while continue_walk && indegree[current].is_zero() "
    "&& !visited[current] {\n"
)
NEW_OUTER = (
    "        while continue_walk && indegree[current].is_zero() "
    "&& visited[current] == 0 {\n"
)
OLD_INNER = "            while k <= 5 || visited[current] {\n"
NEW_INNER = "            while k <= 5 || visited[current] != 0 {\n"
OLD_BRANCH = "                if visited[current] {\n"
NEW_BRANCH = "                if visited[current] != 0 {\n"
OLD_ASSIGN = "                    visited[vertex] = true;\n"
NEW_ASSIGN = "                    visited[vertex] = 1;\n"
TEST_MODULE = '''

#[cfg(test)]
mod byte_forest_visited_tests {
    use super::split_forest;

    #[test]
    fn byte_visit_storage_preserves_reference_splits() {
        for parent in [
            vec![1, 2, 3, 4, 5, 6, 7, 7],
            vec![0, 2, 2, 4, 4, 5, 7, 7],
            vec![1, 1, 3, 3, 4, 6, 6, 8, 8, 9],
        ] {
            let split = split_forest(&parent).unwrap();
            assert_eq!(split.len(), parent.len());
            assert!(split.iter().all(|target| *target < split.len()));
        }
    }
}
'''


def apply_candidate(source):
    candidate = source
    for old, new, name in (
        (OLD_VISITED, NEW_VISITED, "visited allocation"),
        (OLD_OUTER, NEW_OUTER, "outer visited condition"),
        (OLD_INNER, NEW_INNER, "inner visited condition"),
        (OLD_BRANCH, NEW_BRANCH, "visited branch"),
    ):
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_ASSIGN) != 2:
        raise RuntimeError(
            f"expected two visited assignments, found {candidate.count(OLD_ASSIGN)}"
        )
    candidate = candidate.replace(OLD_ASSIGN, NEW_ASSIGN)
    if "mod byte_forest_visited_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    peak_ratio = result.get("geometric_additional_peak_ratio", 1.0)
    checkpoint = f'''### Byte forest-visited checkpoint — 2026-08-24

- Replacing bit-packed visited flags with byte flags was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric direct-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak ratio: `{peak_ratio:.3f}x`; worst process-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/byte-forest-visited-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Byte forest-visited checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Byte forest-visited gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Direct-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / worst RSS ratios: `{peak_ratio:.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/byte-forest-visited-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Byte forest-visited gate\n"
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
    "experiment": "byte-forest-visited-v4",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-byte-visited-baseline"))
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

    candidate = build(Path("/tmp/cmg-byte-visited-candidate"))
    split_specs = (
        ("path-500k", ["path", "500000", "5"]),
        ("path-1m", ["path", "1000000", "5"]),
        ("segmented-path-2m", ["segmented-path", "2000000", "5"]),
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
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, arguments, name
        )

    split_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["split_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
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
        for collection in (
            result["split_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["path_hierarchy_time_ratio"] = result["hierarchy_cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["worst_split_time_ratio"] = max(split_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["improved_split_case_count"] = sum(
        ratio < 1.0 for ratio in split_ratios
    )
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.94,
        "hierarchy_geometric_time_ratio_max": 0.99,
        "path_hierarchy_time_ratio_max": 0.975,
        "worst_split_time_ratio_max": 1.02,
        "worst_hierarchy_time_ratio_max": 1.03,
        "geometric_additional_peak_ratio_max": 1.025,
        "worst_additional_peak_ratio_max": 1.04,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "worst_peak_rss_ratio_max": 1.04,
        "improved_split_case_count_min": 3,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.94
        and result["hierarchy_geometric_time_ratio"] <= 0.99
        and result["path_hierarchy_time_ratio"] <= 0.975
        and result["worst_split_time_ratio"] <= 1.02
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["geometric_additional_peak_ratio"] <= 1.025
        and result["worst_additional_peak_ratio"] <= 1.04
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["worst_peak_rss_ratio"] <= 1.04
        and result["improved_split_case_count"] >= 3
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; byte flags materially reduced forest-split and path hierarchy time within the temporary-memory budget"
        if result["accepted"]
        else "validation passed, but speed or temporary-memory gates were not all met"
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
    "worst_split_time_ratio",
    "worst_hierarchy_time_ratio",
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_split_case_count", 0)
result.setdefault("max_post_drop_delta_bytes", 0)
RECORD.parent.mkdir(parents=True, exist_ok=True)
RECORD.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
update_documents(result)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
for stale in (
    ".github/workflows/byte-forest-visited.yml",
    ".github/workflows/byte-forest-visited-v2.yml",
    ".github/workflows/byte-forest-visited-v3.yml",
    "scripts/byte_forest_visited_gate.py",
    "scripts/byte_forest_visited_gate_v2.py",
    "scripts/byte_forest_visited_gate_v3.py",
):
    Path(stale).unlink(missing_ok=True)
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
    "perf: retain byte forest visited flags"
    if result.get("accepted", False)
    else "perf: record byte forest-visited experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push byte forest-visited decision")
