import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
FOREST = Path("src/forest.rs")
LIB = Path("src/lib.rs")
TEMP_BENCH = Path("src/bin/forest-walk-buffer-gate.rs")
WORKFLOW = Path(".github/workflows/inline-forest-walk.yml")
SCRIPT = Path("scripts/inline_forest_walk_gate.py")
RECORD = Path(".ci/performance/inline-forest-walk-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

PROFILE_EXPORT = r'''

#[cfg(feature = "profiling")]
/// Apply the trusted forest splitter for benchmark-only phase qualification.
pub fn profile_split_forest_kernel(parent: &[usize]) -> Result<Vec<usize>, CmgError> {
    split_forest_trusted(parent)
}
'''
LIB_EXPORT_MARKER = '''#[cfg(feature = "profiling")]
pub use pcg_profile::{PcgPhaseProfile, PcgPhaseSample, ProfiledPcgResult, profile_pcg_with_plan};
'''
LIB_PROFILE_EXPORT = '''#[cfg(feature = "profiling")]
pub use forest::profile_split_forest_kernel;
'''

BENCH_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgHierarchy, CmgOptions, Laplacian, maximum_weight_forest,
    profile_split_forest_kernel,
};

struct BenchGraph {
    graph: Laplacian,
    input_edges: usize,
}

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
        input_edges,
    }
}

fn worker_firm_graph(per_side: usize, degree: usize) -> BenchGraph {
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
    let input_edges = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).unwrap(),
        input_edges,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    }
}

fn checksum(values: &[usize]) -> u64 {
    values.iter().enumerate().fold(0_u64, |state, (index, value)| {
        state
            .wrapping_mul(0x9e37_79b1_85eb_ca87)
            .wrapping_add((index as u64).rotate_left(17))
            .wrapping_add(*value as u64)
    })
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap();
    let scale = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let bench_graph = build_case(&case, scale);
    let hierarchy = CmgHierarchy::build(&bench_graph.graph, CmgOptions::default()).unwrap();
    let parents: Vec<_> = hierarchy
        .levels()
        .iter()
        .filter(|level| level.aggregation().is_some())
        .map(|level| maximum_weight_forest(level.graph()).0)
        .collect();
    let total_parent_vertices: usize = parents.iter().map(Vec::len).sum();

    let mut expected_checksum = 0_u64;
    for parent in &parents {
        let split = profile_split_forest_kernel(parent).unwrap();
        expected_checksum = expected_checksum
            .wrapping_mul(0x517c_c1b7_2722_0a95)
            .wrapping_add(checksum(&split));
        black_box(split);
    }

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let mut observed_checksum = 0_u64;
        for parent in &parents {
            let split = profile_split_forest_kernel(black_box(parent)).unwrap();
            observed_checksum = observed_checksum
                .wrapping_mul(0x517c_c1b7_2722_0a95)
                .wrapping_add(checksum(&split));
            black_box(split);
        }
        elapsed.push(start.elapsed().as_nanos());
        assert_eq!(observed_checksum, expected_checksum);
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"input_edges\":{},\"vertices\":{},\"edges\":{},\"levels\":{},\"parent_sets\":{},\"total_parent_vertices\":{total_parent_vertices},\"repetitions\":{repetitions},\"median_ns\":{},\"checksum\":{expected_checksum}}}",
        bench_graph.input_edges,
        bench_graph.graph.vertex_count(),
        bench_graph.graph.edge_count(),
        hierarchy.levels().len(),
        parents.len(),
        median(elapsed),
    );
}
'''

INLINE_BUFFER = r'''
const INLINE_FOREST_WALK_CAPACITY: usize = 16;

struct ForestWalkBuffer {
    inline_vertices: [usize; INLINE_FOREST_WALK_CAPACITY],
    inline_ancestors: [i64; INLINE_FOREST_WALK_CAPACITY],
    spill_vertices: Vec<usize>,
    spill_ancestors: Vec<i64>,
    len: usize,
    spilled: bool,
}

impl Default for ForestWalkBuffer {
    fn default() -> Self {
        Self {
            inline_vertices: [0; INLINE_FOREST_WALK_CAPACITY],
            inline_ancestors: [0; INLINE_FOREST_WALK_CAPACITY],
            spill_vertices: Vec::new(),
            spill_ancestors: Vec::new(),
            len: 0,
            spilled: false,
        }
    }
}

impl ForestWalkBuffer {
    #[inline]
    fn reset(&mut self, vertex: usize) {
        self.inline_vertices[0] = vertex;
        self.inline_ancestors[0] = 0;
        self.len = 1;
        self.spilled = false;
    }

    #[inline]
    fn push(&mut self, vertex: usize, ancestors: i64) {
        if !self.spilled && self.len < INLINE_FOREST_WALK_CAPACITY {
            self.inline_vertices[self.len] = vertex;
            self.inline_ancestors[self.len] = ancestors;
        } else {
            if !self.spilled {
                self.spill_vertices.clear();
                self.spill_ancestors.clear();
                self.spill_vertices
                    .extend_from_slice(&self.inline_vertices[..self.len]);
                self.spill_ancestors
                    .extend_from_slice(&self.inline_ancestors[..self.len]);
                self.spilled = true;
            }
            self.spill_vertices.push(vertex);
            self.spill_ancestors.push(ancestors);
        }
        self.len += 1;
    }

    #[inline]
    fn vertex(&self, index: usize) -> usize {
        debug_assert!(index < self.len);
        if self.spilled {
            self.spill_vertices[index]
        } else {
            self.inline_vertices[index]
        }
    }

    #[inline]
    fn ancestors(&self, index: usize) -> i64 {
        debug_assert!(index < self.len);
        if self.spilled {
            self.spill_ancestors[index]
        } else {
            self.inline_ancestors[index]
        }
    }
}

'''

INSERT_MARKER = '''fn split_forest_impl_with_indegree<I: ForestIndegree>(
'''
OLD_DECLARATIONS = '''    let mut walk = Vec::new();
    let mut new_ancestors = Vec::new();
'''
NEW_DECLARATIONS = '''    let mut walk = ForestWalkBuffer::default();
'''
OLD_RESET = '''            walk.clear();
            walk.push(current);
            new_ancestors.clear();
            new_ancestors.push(0_i64);
'''
NEW_RESET = '''            walk.reset(current);
'''
OLD_WALK = '''            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == walk[k] || (k > 0 && current == walk[k - 1]);
                if terminated {
                    break;
                }
                k += 1;
                walk.push(current);
                if visited[current] {
                    new_ancestors.push(ancestors_in_path);
                } else {
                    ancestors_in_path += 1;
                    new_ancestors.push(ancestors_in_path);
                }
            }
'''
NEW_WALK = '''            while k <= 5 || visited[current] {
                current = forest[current];
                let terminated = current == walk.vertex(k)
                    || (k > 0 && current == walk.vertex(k - 1));
                if terminated {
                    break;
                }
                k += 1;
                if !visited[current] {
                    ancestors_in_path += 1;
                }
                walk.push(current, ancestors_in_path);
            }
'''
OLD_DIAMETER = '''            if k > 5 {
                let middle = k / 2;
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
                current = next;
                continue_walk = true;
            }
'''
NEW_DIAMETER = '''            if k > 5 {
                let middle = k / 2;
                let middle_vertex = walk.vertex(middle);
                forest[middle_vertex] = middle_vertex;
                let next = walk.vertex(middle + 1);
                indegree[next].decrement();
                let removed = ancestors[middle_vertex];
                for index in (middle + 1)..=k {
                    ancestors[walk.vertex(index)] -= removed;
                }
                for index in 0..=middle {
                    let vertex = walk.vertex(index);
                    visited[vertex] = true;
                    ancestors[vertex] += walk.ancestors(index);
                }
                current = next;
                continue_walk = true;
            }
'''
OLD_FINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
            }
'''
NEW_FINAL = '''            if !continue_walk {
                for index in 0..=k {
                    let vertex = walk.vertex(index);
                    ancestors[vertex] += walk.ancestors(index);
                    visited[vertex] = true;
                }
            }
'''
TEST_MODULE = r'''

#[cfg(test)]
mod inline_forest_walk_tests {
    use super::{ForestWalkBuffer, INLINE_FOREST_WALK_CAPACITY};

    #[test]
    fn inline_walk_buffer_preserves_values_and_spills_safely() {
        let mut walk = ForestWalkBuffer::default();
        walk.reset(7);
        for index in 1..(INLINE_FOREST_WALK_CAPACITY + 19) {
            walk.push(7 + index, index as i64);
        }
        for index in 0..(INLINE_FOREST_WALK_CAPACITY + 19) {
            assert_eq!(walk.vertex(index), 7 + index);
            assert_eq!(walk.ancestors(index), index as i64);
        }

        walk.reset(3);
        assert_eq!(walk.vertex(0), 3);
        assert_eq!(walk.ancestors(0), 0);
        for index in 1..INLINE_FOREST_WALK_CAPACITY {
            walk.push(3 + index, -(index as i64));
        }
        for index in 0..INLINE_FOREST_WALK_CAPACITY {
            assert_eq!(walk.vertex(index), 3 + index);
            assert_eq!(walk.ancestors(index), -(index as i64));
        }
    }
}
'''


def apply_candidate(source):
    candidate = source
    replacements = (
        (INSERT_MARKER, INLINE_BUFFER + INSERT_MARKER, "inline buffer insertion"),
        (OLD_DECLARATIONS, NEW_DECLARATIONS, "walk declarations"),
        (OLD_RESET, NEW_RESET, "walk reset"),
        (OLD_WALK, NEW_WALK, "diameter walk"),
        (OLD_DIAMETER, NEW_DIAMETER, "diameter cut updates"),
        (OLD_FINAL, NEW_FINAL, "final ancestor updates"),
    )
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod inline_forest_walk_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def instrument(source, lib_source):
    if "pub fn profile_split_forest_kernel" in source:
        raise RuntimeError("temporary split benchmark export already exists")
    if LIB_EXPORT_MARKER not in lib_source:
        raise RuntimeError("profiling export marker changed unexpectedly")
    return (
        source.rstrip() + PROFILE_EXPORT + "\n",
        lib_source.replace(LIB_EXPORT_MARKER, LIB_EXPORT_MARKER + LIB_PROFILE_EXPORT, 1),
    )


def run_build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run([
        "cargo",
        "build",
        "--release",
        "--features",
        "profiling",
        "--bin",
        "forest-walk-buffer-gate",
    ], env=env)
    run([
        "cargo",
        "build",
        "--release",
        "--manifest-path",
        "benchmarks/Cargo.toml",
        "--bin",
        "hierarchy-build",
        "--bin",
        "hierarchy-alloc",
    ], env=env)
    release = target / "release"
    return {
        "split": release / "forest-walk-buffer-gate",
        "hierarchy": release / "hierarchy-build",
        "allocation": release / "hierarchy-alloc",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-inline-walk-{tag}.time")
    completed = run([
        "/usr/bin/time",
        "-v",
        "-o",
        time_path,
        binary,
        *arguments,
    ])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected benchmark output for {tag}: {payloads}")
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
    for index, (label, binary) in enumerate((
        ("baseline", baseline[kind]),
        ("candidate", candidate[kind]),
        ("candidate", candidate[kind]),
        ("baseline", baseline[kind]),
    )):
        observation = sample(binary, arguments, f"{kind}-{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    if kind == "split":
        stable = (
            "case", "scale", "input_edges", "vertices", "edges", "levels",
            "parent_sets", "total_parent_vertices", "repetitions", "checksum",
        )
    elif kind == "hierarchy":
        stable = ("case", "scale", "vertices", "edges", "repetitions")
    else:
        stable = (
            "case", "scale", "vertices", "edges", "repetitions", "levels",
            "hierarchy_matrix_nonzeros", "max_post_drop_delta_bytes",
        )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": statistics.median(item["median_ns"] for item in baseline_samples),
        "candidate_median_ns": statistics.median(item["median_ns"] for item in candidate_samples),
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
    checkpoint = f'''### Inline forest-walk checkpoint — 2026-08-24

- A 16-entry inline diameter-walk buffer with safe heap spill was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; baseline/candidate split checksums and hierarchy metadata were identical.
- Geometric trusted-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst hierarchy time / process-RSS ratios: `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/inline-forest-walk-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Inline forest-walk checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Refresh cumulative retained optimization and memory guidance.\n"
        "2. Re-profile hierarchy setup only if the inline buffer is retained.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Defer further forest mutations unless a new profile shows a material stable target.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Inline forest-walk gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Trusted-split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Exact additional-peak / retained ratios: `{result.get("geometric_additional_peak_ratio", 1.0):.3f}x` / `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/inline-forest-walk-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Inline forest-walk gate\n"
    if heading in status:
        start = status.index(heading)
        end = status.find("\n## ", start + len(heading))
        if end == -1:
            end = len(status)
        status = status[:start] + block + status[end:]
    else:
        status += "\n\n" + block
    STATUS.write_text(status.rstrip() + "\n")


original_forest = FOREST.read_text()
original_lib = LIB.read_text()
production_candidate = apply_candidate(original_forest)
result = {
    "schema_version": 1,
    "experiment": "inline-forest-diameter-walk-buffer",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
    "allocation_cases": {},
}

try:
    TEMP_BENCH.parent.mkdir(parents=True, exist_ok=True)
    TEMP_BENCH.write_text(BENCH_SOURCE)
    instrumented_forest, instrumented_lib = instrument(original_forest, original_lib)
    FOREST.write_text(instrumented_forest)
    LIB.write_text(instrumented_lib)
    run(["cargo", "fmt", "--all"])
    baseline = run_build(Path("/tmp/cmg-inline-walk-baseline"))

    instrumented_candidate, instrumented_candidate_lib = instrument(
        production_candidate,
        original_lib,
    )
    FOREST.write_text(instrumented_candidate)
    LIB.write_text(instrumented_candidate_lib)
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
    candidate = run_build(Path("/tmp/cmg-inline-walk-candidate"))
    result["validation"] = "success"

    specs = (
        ("path-1m", "path", "1000000"),
        ("worker-firm-1.5m", "worker-firm", "500000"),
        ("worker-firm-3m", "worker-firm", "1000000"),
        ("dense-worker-firm-1.6m", "dense-worker-firm", "100000"),
    )
    for name, case, scale in specs:
        result["split_cases"][name] = compare(
            "split", baseline, candidate, [case, scale, "5"], name
        )
        result["hierarchy_cases"][name] = compare(
            "hierarchy", baseline, candidate, [case, scale, "3"], name
        )
        result["allocation_cases"][name] = compare(
            "allocation", baseline, candidate, [case, scale, "2"], name
        )

    split_ratios = [case["candidate_over_baseline_time"] for case in result["split_cases"].values()]
    hierarchy_ratios = [case["candidate_over_baseline_time"] for case in result["hierarchy_cases"].values()]
    additional_peak = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["allocation_cases"].values()
    ]
    retained = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["allocation_cases"].values()
    ]
    rss = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["split_cases"], result["hierarchy_cases"], result["allocation_cases"])
        for case in collection.values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["worst_split_time_ratio"] = max(split_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["geometric_additional_peak_ratio"] = geometric(additional_peak)
    result["worst_additional_peak_ratio"] = max(additional_peak)
    result["geometric_retained_ratio"] = geometric(retained)
    result["worst_retained_ratio"] = max(retained)
    result["worst_peak_rss_ratio"] = max(rss)
    result["improved_split_case_count"] = sum(value < 1.0 for value in split_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["allocation_cases"].values()
    )
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.98,
        "hierarchy_geometric_time_ratio_max": 0.995,
        "worst_split_time_ratio_max": 1.03,
        "worst_hierarchy_time_ratio_max": 1.03,
        "improved_split_case_count_min": 3,
        "geometric_additional_peak_ratio_max": 1.001,
        "worst_additional_peak_ratio_max": 1.003,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "worst_peak_rss_ratio_max": 1.02,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.98
        and result["hierarchy_geometric_time_ratio"] <= 0.995
        and result["worst_split_time_ratio"] <= 1.03
        and result["worst_hierarchy_time_ratio"] <= 1.03
        and result["improved_split_case_count"] >= 3
        and result["geometric_additional_peak_ratio"] <= 1.001
        and result["worst_additional_peak_ratio"] <= 1.003
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; common short diameter walks stay inline with a safe arbitrary-length spill path"
        if result["accepted"]
        else "correctness passed, but direct-split, full-hierarchy, or memory limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

TEMP_BENCH.unlink(missing_ok=True)
if result.get("accepted", False):
    FOREST.write_text(production_candidate)
    LIB.write_text(original_lib)
    try:
        run(["cargo", "fmt", "--all"])
        run(["cargo", "fmt", "--all", "--", "--check"])
        run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
        run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    except Exception as error:
        result["accepted"] = False
        result["validation"] = "failure"
        result["error"] = repr(error)
        result["decision_reason"] = f"final production-tree validation failed safely: {error}"
        FOREST.write_text(original_forest)
        LIB.write_text(original_lib)
        run(["cargo", "fmt", "--all"], check=False)
else:
    FOREST.write_text(original_forest)
    LIB.write_text(original_lib)
    run(["cargo", "fmt", "--all"], check=False)

for key in (
    "split_geometric_time_ratio", "hierarchy_geometric_time_ratio",
    "worst_split_time_ratio", "worst_hierarchy_time_ratio",
    "geometric_additional_peak_ratio", "worst_additional_peak_ratio",
    "geometric_retained_ratio", "worst_retained_ratio", "worst_peak_rss_ratio",
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
    "perf: retain inline forest walk buffer"
    if result.get("accepted", False)
    else "perf: record inline forest-walk experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push inline forest-walk decision")
if result.get("validation") == "failure":
    raise SystemExit(1)
