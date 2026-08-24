import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/packed-forest-visit-map-gate.rs")
WORKFLOW = Path(".github/workflows/packed-forest-visit-map.yml")
SCRIPT = Path("scripts/packed_forest_visit_map_gate.py")
RECORD = Path(".ci/performance/packed-forest-visit-map-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCH_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{CmgHierarchy, CmgOptions, Laplacian, maximum_weight_forest, split_forest};

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> Laplacian {
    Laplacian::from_edges(
        vertices,
        (0..vertices.saturating_sub(1)).map(|vertex| (vertex, vertex + 1, 1.0)),
    )
    .unwrap()
}

fn worker_firm_graph(per_side: usize, degree: usize) -> Laplacian {
    let vertices = 2 * per_side;
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(degree * per_side);
    for worker in 0..per_side {
        for link in 0..degree {
            let firm = if link == 0 {
                worker
            } else if link == 1 {
                (worker + 1) % per_side
            } else {
                ((2 * link + 1) * worker + 17 * link + 3) % per_side
            };
            let weight = 0.25 + ((worker + 7 * link) % 23) as f64 / 16.0;
            edges.push((worker, firm_offset + firm, weight));
        }
    }
    Laplacian::from_edges(vertices, edges).unwrap()
}

fn build_graph(case: &str, scale: usize) -> Laplacian {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    }
}

fn checksum(values: &[usize]) -> u64 {
    values.iter().enumerate().fold(0_u64, |value, (index, item)| {
        value
            .wrapping_mul(0x9e37_79b1_85eb_ca87)
            .wrapping_add((index as u64).rotate_left(17))
            .wrapping_add(*item as u64)
    })
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap();
    let scale = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let graph = build_graph(&case, scale);
    let hierarchy = CmgHierarchy::build(&graph, CmgOptions::default()).unwrap();
    let parent_sets: Vec<Vec<usize>> = hierarchy
        .levels()
        .iter()
        .filter(|level| level.aggregation().is_some())
        .map(|level| maximum_weight_forest(level.graph()).0)
        .collect();
    let reference_checksums: Vec<u64> = parent_sets
        .iter()
        .map(|parent| checksum(&split_forest(parent).unwrap()))
        .collect();
    let total_parent_vertices: usize = parent_sets.iter().map(Vec::len).sum();

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let observed: Vec<u64> = parent_sets
            .iter()
            .map(|parent| checksum(&split_forest(black_box(parent)).unwrap()))
            .collect();
        elapsed.push(start.elapsed().as_nanos());
        assert_eq!(observed, reference_checksums);
        black_box(observed);
    }

    let combined_checksum = reference_checksums.iter().fold(0_u64, |value, item| {
        value
            .wrapping_mul(0x517c_c1b7_2722_0a95)
            .wrapping_add(*item)
    });
    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"parent_sets\":{},\"total_parent_vertices\":{total_parent_vertices},\"repetitions\":{repetitions},\"median_ns\":{},\"checksum\":{combined_checksum}}}",
        graph.vertex_count(),
        graph.edge_count(),
        hierarchy.levels().len(),
        parent_sets.len(),
        median(elapsed),
    );
}
'''

VISIT_MAP = '''struct ForestVisitMap {
    words: Vec<u64>,
}

impl ForestVisitMap {
    fn new(vertex_count: usize) -> Self {
        Self {
            words: vec![0_u64; vertex_count.div_ceil(64)],
        }
    }

    #[inline]
    fn is_set(&self, vertex: usize) -> bool {
        let word = vertex / 64;
        let bit = vertex % 64;
        self.words[word] & (1_u64 << bit) != 0
    }

    #[inline]
    fn set(&mut self, vertex: usize) {
        let word = vertex / 64;
        let bit = vertex % 64;
        self.words[word] |= 1_u64 << bit;
    }
}

'''
INSERT_MARKER = '''trait ForestIndegree: Copy {
'''
OLD_INIT = '''    let mut visited = vec![false; n];
'''
NEW_INIT = '''    let mut visited = ForestVisitMap::new(n);
'''
OLD_OUTER = '''        while continue_walk && indegree[current].is_zero() && !visited[current] {
'''
NEW_OUTER = '''        while continue_walk && indegree[current].is_zero() && !visited.is_set(current) {
'''
OLD_INNER = '''            while k <= 5 || visited[current] {
'''
NEW_INNER = '''            while k <= 5 || visited.is_set(current) {
'''
OLD_RECORD = '''                ancestors_in_path += i64::from(u8::from(!visited[current]));
'''
NEW_RECORD = '''                ancestors_in_path += i64::from(u8::from(!visited.is_set(current)));
'''
OLD_SET = '''                    visited[vertex] = true;
'''
NEW_SET = '''                    visited.set(vertex);
'''
TEST_MODULE = '''

#[cfg(test)]
mod packed_forest_visit_map_tests {
    use super::ForestVisitMap;

    #[test]
    fn packed_visit_map_crosses_word_boundaries() {
        let mut visited = ForestVisitMap::new(130);
        for vertex in [0, 1, 63, 64, 65, 127, 128, 129] {
            assert!(!visited.is_set(vertex));
            visited.set(vertex);
            assert!(visited.is_set(vertex));
        }
        assert!(!visited.is_set(62));
        assert!(!visited.is_set(66));
        assert!(!visited.is_set(126));
    }
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


def apply_candidate(source):
    if source.count(INSERT_MARKER) != 1:
        raise RuntimeError("ForestIndegree insertion marker changed unexpectedly")
    candidate = source.replace(INSERT_MARKER, VISIT_MAP + INSERT_MARKER, 1)
    replacements = (
        (OLD_INIT, NEW_INIT, "visited initialization"),
        (OLD_OUTER, NEW_OUTER, "outer visited condition"),
        (OLD_INNER, NEW_INNER, "inner visited condition"),
        (OLD_RECORD, NEW_RECORD, "ancestor recording visit lookup"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if candidate.count(OLD_SET) != 2:
        raise RuntimeError(
            f"expected two visited writes, found {candidate.count(OLD_SET)}"
        )
    candidate = candidate.replace(OLD_SET, NEW_SET)
    if "mod packed_forest_visit_map_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(["cargo", "build", "--release", "--bin", "packed-forest-visit-map-gate"], env=env)
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
        "split": release / "packed-forest-visit-map-gate",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-packed-visit-{tag}.time")
    completed = run(["/usr/bin/time", "-v", "-o", time_path, binary, *arguments])
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
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline[kind]),
            ("candidate", candidate[kind]),
            ("candidate", candidate[kind]),
            ("baseline", baseline[kind]),
        )
    ):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    if kind == "split":
        stable = (
            "case",
            "scale",
            "vertices",
            "edges",
            "levels",
            "parent_sets",
            "total_parent_vertices",
            "repetitions",
            "checksum",
        )
    elif kind == "hierarchy":
        stable = ("case", "scale", "vertices", "edges", "repetitions")
    else:
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
                raise RuntimeError(f"{name}: {kind} changed {key}")

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


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Packed forest visit-map checkpoint — 2026-08-24

- Replacing `Vec<bool>` access with an explicit `u64` packed visit map was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums, hierarchy metadata, and final certificates were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/packed-forest-visit-map-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Packed forest visit-map checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile split subphases if the packed visit map is retained; otherwise test a fused walk/prefix scratch layout.\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact split parents and complete hierarchy diagnostics in every gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Packed forest visit-map gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/packed-forest-visit-map-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Packed forest visit-map gate\n"
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
TEMP_BENCH.write_text(BENCH_SOURCE)
result = {
    "schema_version": 1,
    "experiment": "explicit-packed-forest-visit-map",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-packed-visit-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run(["cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    run(["cargo", "build", "--release", "--all-features"])
    result["validation"] = "success"

    candidate = build(Path("/tmp/cmg-packed-visit-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "4"]),
    )
    for name, arguments in specs:
        result["split_cases"][name] = compare(
            "split", baseline, candidate, arguments, name
        )
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, arguments, name
        )
        allocation_arguments = [arguments[0], arguments[1], "3"]
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, allocation_arguments, name
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
        for collection in (
            result["split_cases"],
            result["hierarchy_cases"],
            result["allocation_cases"],
        )
        for case in collection.values()
    ]
    additional_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["path_hierarchy_time_ratio"] = result["hierarchy_cases"]["path-1m"][
        "candidate_over_baseline_time"
    ]
    result["worst_split_time_ratio"] = max(split_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["geometric_additional_peak_ratio"] = geometric(additional_peak)
    result["worst_additional_peak_ratio"] = max(additional_peak)
    result["geometric_retained_ratio"] = geometric(retained)
    result["worst_retained_ratio"] = max(retained)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["improved_split_case_count"] = sum(value < 1.0 for value in split_ratios)
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.98,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "path_hierarchy_time_ratio_max": 0.99,
        "worst_split_time_ratio_max": 1.025,
        "worst_hierarchy_time_ratio_max": 1.03,
        "worst_peak_rss_ratio_max": 1.02,
        "geometric_additional_peak_ratio_max": 1.001,
        "worst_additional_peak_ratio_max": 1.003,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "improved_split_case_count_min": 3,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.98
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["path_hierarchy_time_ratio"] <= 0.99
        and result["worst_split_time_ratio"] <= 1.025
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["geometric_additional_peak_ratio"] <= 1.001
        and result["worst_additional_peak_ratio"] <= 1.003
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["improved_split_case_count"] >= 3
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; explicit packed visit access reduced revisit-heavy splitter cost without increasing hierarchy memory"
        if result["accepted"]
        else "correctness passed, but split, hierarchy, or exact/process memory gates were not all met"
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
    "geometric_additional_peak_ratio",
    "worst_additional_peak_ratio",
    "geometric_retained_ratio",
    "worst_retained_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_split_case_count", 0)
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
    "perf: retain explicit packed forest visit map"
    if result.get("accepted", False)
    else "perf: record packed forest visit-map experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push packed forest visit-map decision")

if result.get("validation") != "success":
    raise SystemExit(1)
