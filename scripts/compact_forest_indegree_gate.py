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
WORKFLOW = Path(".github/workflows/compact-forest-indegree.yml")
SCRIPT = Path("scripts/compact_forest_indegree_gate.py")
RECORD = Path(".ci/performance/compact-forest-indegree-latest.json")
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
            "hierarchy-build",
        ],
        env=env,
    )
    return {
        "split": target / "release" / "forest-split-gate",
        "hierarchy": target / "release" / "hierarchy-build",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-compact-indegree-{tag}.time")
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

    stable = (
        ("case", "vertices", "repetitions", "checksum")
        if kind == "split"
        else ("case", "scale", "vertices", "edges", "repetitions")
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
    return {
        "arguments": arguments,
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
        "metadata": {key: reference[key] for key in stable},
    }


OLD_SIGNATURE = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if validate {
'''
NEW_SIGNATURE = '''trait ForestIndegree: Copy {
    const ZERO: Self;

    fn is_zero(self) -> bool;
    fn increment(&mut self);
    fn decrement(&mut self);
}

macro_rules! impl_forest_indegree {
    ($type:ty) => {
        impl ForestIndegree for $type {
            const ZERO: Self = 0;

            #[inline]
            fn is_zero(self) -> bool {
                self == 0
            }

            #[inline]
            fn increment(&mut self) {
                *self = self.checked_add(1).expect("forest indegree overflow");
            }

            #[inline]
            fn decrement(&mut self) {
                *self = self.checked_sub(1).expect("forest indegree invariant");
            }
        }
    };
}

impl_forest_indegree!(u32);
impl_forest_indegree!(usize);

fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
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
'''
OLD_VECTOR = '''    let mut indegree = vec![0_usize; n];
'''
NEW_VECTOR = '''    let mut indegree = vec![I::ZERO; n];
'''
OLD_INCREMENT = '''    for &target in &forest {
        indegree[target] += 1;
    }
'''
NEW_INCREMENT = '''    for &target in &forest {
        indegree[target].increment();
    }
'''
OLD_ZERO = "indegree[current] == 0"
NEW_ZERO = "indegree[current].is_zero()"
OLD_DECREMENT = '''                indegree[next] = indegree[next]
                    .checked_sub(1)
                    .expect("forest indegree invariant");
'''
NEW_DECREMENT = '''                indegree[next].decrement();
'''
TEST_MODULE = '''

#[cfg(test)]
mod compact_forest_indegree_tests {
    use super::split_forest;

    #[test]
    fn compact_indegree_path_preserves_split_result() {
        let parent = vec![1, 2, 3, 4, 5, 6, 7, 7, 9, 10, 11, 11];
        let result = split_forest(&parent).unwrap();
        assert_eq!(result.len(), parent.len());
        assert!(result.iter().enumerate().all(|(vertex, target)| {
            *target < result.len() && (*target == vertex || parent[vertex] == *target)
        }));
    }
}
'''


def apply_candidate(source):
    replacements = (
        (OLD_SIGNATURE, NEW_SIGNATURE, "split implementation signature"),
        (OLD_VECTOR, NEW_VECTOR, "indegree vector"),
        (OLD_INCREMENT, NEW_INCREMENT, "indegree construction"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_ZERO) != 2:
        raise RuntimeError("expected two indegree zero tests")
    candidate = candidate.replace(OLD_ZERO, NEW_ZERO)
    if candidate.count(OLD_DECREMENT) != 2:
        raise RuntimeError("expected two indegree decrements")
    candidate = candidate.replace(OLD_DECREMENT, NEW_DECREMENT)
    if "mod compact_forest_indegree_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Compact forest-indegree checkpoint — 2026-08-23

- Monomorphized `u32` forest indegrees with a native-width fallback were **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/compact-forest-indegree-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Compact forest-indegree checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Compact forest-indegree gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/compact-forest-indegree-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Compact forest-indegree gate\n"
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
    "experiment": "compact-forest-indegree",
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
    baseline = build(Path("/tmp/cmg-compact-indegree-baseline"))
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

    candidate = build(Path("/tmp/cmg-compact-indegree-candidate"))
    split_specs = (
        ("path-500k", ["path", "500000", "4"]),
        ("path-1m", ["path", "1000000", "4"]),
        ("paired-path-2m", ["paired-path", "2000000", "4"]),
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

    split_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["split_cases"].values()
    ]
    hierarchy_ratios = [
        case["candidate_over_baseline_time"]
        for case in result["hierarchy_cases"].values()
    ]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["split_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["path_hierarchy_time_ratio"] = result["hierarchy_cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["worst_split_time_ratio"] = max(split_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_split_case_count"] = sum(value < 1.0 for value in split_ratios)
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.95,
        "path_hierarchy_time_ratio_max": 0.985,
        "hierarchy_geometric_time_ratio_max": 0.997,
        "worst_split_time_ratio_max": 1.02,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.01,
        "improved_split_case_count_min": 2,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.95
        and result["path_hierarchy_time_ratio"] <= 0.985
        and result["hierarchy_geometric_time_ratio"] <= 0.997
        and result["worst_split_time_ratio"] <= 1.02
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.01
        and result["improved_split_case_count"] >= 2
    )
    result["decision_reason"] = (
        "full qualification passed; compact forest indegrees reduced split bandwidth and path hierarchy time with a native fallback"
        if result["accepted"]
        else "qualification passed, but split or complete hierarchy timing did not improve consistently enough"
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
    "worst_peak_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_split_case_count", 0)
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
    "perf: retain compact forest indegrees"
    if result.get("accepted", False)
    else "perf: record compact forest-indegree experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push compact forest-indegree decision")
