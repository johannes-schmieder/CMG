import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/compact-walk-ancestor-scratch-gate.rs")
WORKFLOW = Path(".github/workflows/compact-walk-ancestor-scratch.yml")
SCRIPT = Path("scripts/compact_walk_ancestor_scratch_gate.py")
RECORD = Path(".ci/performance/compact-walk-ancestor-scratch-latest.json")
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

ENTRY_TYPES = '''trait ForestWalkEntry: Copy {
    fn from_parts(vertex: usize, ancestor_prefix: i64) -> Self;
    fn vertex(self) -> usize;
    fn ancestor_prefix(self) -> i64;
}

#[derive(Clone, Copy)]
struct CompactForestWalkEntry {
    vertex: u32,
    ancestor_prefix: u32,
}

impl ForestWalkEntry for CompactForestWalkEntry {
    #[inline]
    fn from_parts(vertex: usize, ancestor_prefix: i64) -> Self {
        debug_assert!(vertex <= u32::MAX as usize);
        debug_assert!((0..=i64::from(u32::MAX)).contains(&ancestor_prefix));
        Self {
            vertex: vertex as u32,
            ancestor_prefix: ancestor_prefix as u32,
        }
    }

    #[inline]
    fn vertex(self) -> usize {
        self.vertex as usize
    }

    #[inline]
    fn ancestor_prefix(self) -> i64 {
        i64::from(self.ancestor_prefix)
    }
}

#[derive(Clone, Copy)]
struct NativeForestWalkEntry {
    vertex: usize,
    ancestor_prefix: i64,
}

impl ForestWalkEntry for NativeForestWalkEntry {
    #[inline]
    fn from_parts(vertex: usize, ancestor_prefix: i64) -> Self {
        Self {
            vertex,
            ancestor_prefix,
        }
    }

    #[inline]
    fn vertex(self) -> usize {
        self.vertex
    }

    #[inline]
    fn ancestor_prefix(self) -> i64 {
        self.ancestor_prefix
    }
}

'''
INSERT_MARKER = '''trait ForestIndegree: Copy {
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
'''
NEW_ROUTER = '''fn split_forest_impl(parent: &[usize], validate: bool) -> Result<Vec<usize>, CmgError> {
    if parent.len() <= u32::MAX as usize {
        split_forest_impl_with_storage::<u32, CompactForestWalkEntry>(parent, validate)
    } else {
        split_forest_impl_with_storage::<usize, NativeForestWalkEntry>(parent, validate)
    }
}

fn split_forest_impl_with_storage<I: ForestIndegree, W: ForestWalkEntry>(
    parent: &[usize],
    validate: bool,
) -> Result<Vec<usize>, CmgError> {
'''
OLD_SCRATCH = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_SCRATCH = '''    let mut walk = Vec::<W>::new();
'''
OLD_INIT = '''            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_INIT = '''            walk.clear();
            walk.push(W::from_parts(current, 0));
'''
OLD_TERMINATED = '''                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
'''
NEW_TERMINATED = '''                let terminated = current == walk[k].vertex()
                    || (k > 0 && current == walk[k - 1].vertex());
'''
OLD_PUSH = '''                k += 1;
                walk.push(current);
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                new_ancestors.push(ancestors_in_path);
'''
NEW_PUSH = '''                k += 1;
                ancestors_in_path += i64::from(u8::from(!visited[current]));
                walk.push(W::from_parts(current, ancestors_in_path));
'''
OLD_CUT = '''                let middle = k / 2;
                forest[walk[middle]] = walk[middle];
                let next = walk[middle + 1];
                indegree[next].decrement();
                let removed = ancestors[walk[middle]];
                for &vertex in &walk[(middle + 1)..=k] {
                    ancestors[vertex] -= removed;
                }
                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_CUT = '''                let middle = k / 2;
                let middle_vertex = walk[middle].vertex();
                forest[middle_vertex] = middle_vertex;
                let next = walk[middle + 1].vertex();
                indegree[next].decrement();
                let removed = ancestors[middle_vertex];
                for entry in &walk[(middle + 1)..=k] {
                    ancestors[entry.vertex()] -= removed;
                }
                for entry in &walk[..=middle] {
                    let vertex = entry.vertex();
                    visited[vertex] = true;
                    ancestors[vertex] += entry.ancestor_prefix();
                }
'''
OLD_TERMINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_TERMINAL = '''            if !continue_walk {
                for entry in &walk[..=k] {
                    let vertex = entry.vertex();
                    ancestors[vertex] += entry.ancestor_prefix();
                    visited[vertex] = true;
                }
            }
'''
TEST_MODULE = '''

#[cfg(test)]
mod compact_walk_ancestor_scratch_tests {
    use super::{CompactForestWalkEntry, ForestWalkEntry, NativeForestWalkEntry};

    #[test]
    fn compact_and_native_entries_preserve_values() {
        let compact = CompactForestWalkEntry::from_parts(17, 4);
        let native = NativeForestWalkEntry::from_parts(17, 4);
        assert_eq!(compact.vertex(), native.vertex());
        assert_eq!(compact.ancestor_prefix(), native.ancestor_prefix());
        assert_eq!(std::mem::size_of::<CompactForestWalkEntry>(), 8);
        assert_eq!(std::mem::size_of::<NativeForestWalkEntry>(), 16);
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
    candidate = source.replace(INSERT_MARKER, ENTRY_TYPES + INSERT_MARKER, 1)
    replacements = (
        (OLD_ROUTER, NEW_ROUTER, "split storage router"),
        (OLD_SCRATCH, NEW_SCRATCH, "scratch vectors"),
        (OLD_INIT, NEW_INIT, "walk initialization"),
        (OLD_TERMINATED, NEW_TERMINATED, "termination lookup"),
        (OLD_PUSH, NEW_PUSH, "walk push"),
        (OLD_CUT, NEW_CUT, "diameter cut updates"),
        (OLD_TERMINAL, NEW_TERMINAL, "terminal updates"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "new_ancestors" in candidate:
        raise RuntimeError("separate ancestor-prefix scratch remains")
    if "mod compact_walk_ancestor_scratch_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(["cargo", "build", "--release", "--bin", "compact-walk-ancestor-scratch-gate"], env=env)
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
        "split": release / "compact-walk-ancestor-scratch-gate",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-compact-walk-{tag}.time")
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
        "baseline_peak_rss_kib": max(item["peak_rss_kib"] for item in baseline_samples),
        "candidate_peak_rss_kib": max(item["peak_rss_kib"] for item in candidate_samples),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_peak_rss"] = (
        result["candidate_peak_rss_kib"] / result["baseline_peak_rss_kib"]
    )
    if kind == "allocation":
        for field in ("median_additional_peak_bytes", "median_retained_bytes"):
            baseline_value = statistics.median(item[field] for item in baseline_samples)
            candidate_value = statistics.median(item[field] for item in candidate_samples)
            result[f"baseline_{field}"] = baseline_value
            result[f"candidate_{field}"] = candidate_value
            result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    return result


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Compact walk/ancestor scratch checkpoint — 2026-08-24

- Storing realistic-size walk vertices and ancestor prefixes in one 8-byte entry was **{decision}**, with a native-width fallback above `u32::MAX` vertices.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained hierarchy ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/compact-walk-ancestor-scratch-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Compact walk/ancestor scratch checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile split subphases after any retained compact scratch change.\n"
        "2. Refresh cumulative retained optimization and memory guidance.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Preserve exact split parents and complete hierarchy diagnostics in every gate.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Compact walk/ancestor scratch gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/compact-walk-ancestor-scratch-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Compact walk/ancestor scratch gate\n"
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
    "experiment": "compact-walk-ancestor-scratch",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-compact-walk-baseline"))
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

    candidate = build(Path("/tmp/cmg-compact-walk-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "4"]),
    )
    for name, arguments in specs:
        result["split_cases"][name] = compare("split", baseline, candidate, arguments, name)
        result["hierarchy_cases"][name] = compare("hierarchy", baseline, candidate, arguments, name)
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, [arguments[0], arguments[1], "3"], name
        )

    split_ratios = [case["candidate_over_baseline_time"] for case in result["split_cases"].values()]
    hierarchy_ratios = [case["candidate_over_baseline_time"] for case in result["hierarchy_cases"].values()]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["split_cases"], result["hierarchy_cases"], result["allocation_cases"])
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
        case["metadata"]["max_post_drop_delta_bytes"] for case in result["allocation_cases"].values()
    )
    result["improved_split_case_count"] = sum(value < 1.0 for value in split_ratios)
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.94,
        "hierarchy_geometric_time_ratio_max": 0.985,
        "path_hierarchy_time_ratio_max": 0.99,
        "worst_split_time_ratio_max": 1.02,
        "worst_hierarchy_time_ratio_max": 1.02,
        "worst_peak_rss_ratio_max": 1.025,
        "geometric_additional_peak_ratio_max": 1.001,
        "worst_additional_peak_ratio_max": 1.003,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "improved_split_case_count_min": 3,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.94
        and result["hierarchy_geometric_time_ratio"] <= 0.985
        and result["path_hierarchy_time_ratio"] <= 0.99
        and result["worst_split_time_ratio"] <= 1.02
        and result["worst_hierarchy_time_ratio"] <= 1.02
        and result["worst_peak_rss_ratio"] <= 1.025
        and result["geometric_additional_peak_ratio"] <= 1.001
        and result["worst_additional_peak_ratio"] <= 1.003
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["improved_split_case_count"] >= 3
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; compact fused scratch retained the locality gain without the wide-entry RSS penalty"
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
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain compact walk-ancestor scratch"
    if result.get("accepted", False)
    else "perf: record compact walk-ancestor scratch experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push compact walk-ancestor scratch decision")

if result.get("validation") != "success":
    raise SystemExit(1)
