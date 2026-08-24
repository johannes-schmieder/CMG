import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/zipped-ancestor-updates-gate.rs")
WORKFLOW = Path(".github/workflows/zipped-ancestor-updates-v3.yml")
SCRIPT = Path("scripts/zipped_ancestor_updates_gate_v3.py")
RECORD = Path(".ci/performance/zipped-ancestor-updates-latest.json")
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

OLD_DIAMETER_UPDATE = '''                for index in 0..=middle {
                    let vertex = walk[index];
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestors[index];
                }
'''
NEW_DIAMETER_UPDATE = '''                for (&vertex, &new_ancestor) in walk[..=middle]
                    .iter()
                    .zip(&new_ancestors[..=middle])
                {
                    visited[vertex] = true;
                    ancestors[vertex] += new_ancestor;
                }
'''
OLD_TERMINAL_UPDATE = '''                for index in 0..=k {
                    let vertex = walk[index];
                    ancestors[vertex] += new_ancestors[index];
                    visited[vertex] = true;
                }
'''
NEW_TERMINAL_UPDATE = '''                for (&vertex, &new_ancestor) in
                    walk[..=k].iter().zip(&new_ancestors[..=k])
                {
                    ancestors[vertex] += new_ancestor;
                    visited[vertex] = true;
                }
'''
TEST_MODULE = '''

#[cfg(test)]
mod zipped_ancestor_update_tests {
    #[test]
    fn zipped_updates_match_indexed_updates() {
        let walk = [3_usize, 1, 4, 0];
        let deltas = [0_i64, 1, 1, 2];
        let mut indexed = [0_i64; 5];
        let mut zipped = [0_i64; 5];
        for index in 0..walk.len() {
            indexed[walk[index]] += deltas[index];
        }
        for (&vertex, &delta) in walk.iter().zip(&deltas) {
            zipped[vertex] += delta;
        }
        assert_eq!(zipped, indexed);
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
    replacements = (
        (OLD_DIAMETER_UPDATE, NEW_DIAMETER_UPDATE, "diameter ancestor-update loop"),
        (OLD_TERMINAL_UPDATE, NEW_TERMINAL_UPDATE, "terminal ancestor-update loop"),
    )
    candidate = source
    for old, new, name in replacements:
        if candidate.count(old) != 1:
            raise RuntimeError(f"{name} marker changed unexpectedly")
        candidate = candidate.replace(old, new, 1)
    if "mod zipped_ancestor_update_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def build(target):
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    run(["cargo", "build", "--release", "--bin", "zipped-ancestor-updates-gate"], env=env)
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
        "split": target / "release" / "zipped-ancestor-updates-gate",
        "hierarchy": target / "release" / "hierarchy-build",
    }


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-zipped-ancestor-{tag}.time")
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

    stable = (
        (
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
        if kind == "split"
        else ("case", "scale", "vertices", "edges", "repetitions")
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: {kind} changed {key}")

    baseline_ns = statistics.median(item["median_ns"] for item in baseline_samples)
    candidate_ns = statistics.median(item["median_ns"] for item in candidate_samples)
    baseline_rss = max(item["peak_rss_kib"] for item in baseline_samples)
    candidate_rss = max(item["peak_rss_kib"] for item in candidate_samples)
    return {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_median_ns": baseline_ns,
        "candidate_median_ns": candidate_ns,
        "candidate_over_baseline_time": candidate_ns / baseline_ns,
        "baseline_peak_rss_kib": baseline_rss,
        "candidate_peak_rss_kib": candidate_rss,
        "candidate_over_baseline_peak_rss": candidate_rss / baseline_rss,
    }


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    split_ratio = result.get("split_geometric_time_ratio", 1.0)
    hierarchy_ratio = result.get("hierarchy_geometric_time_ratio", 1.0)
    checkpoint = f'''### Zipped ancestor-update checkpoint — 2026-08-24

- Replacing two indexed walk/prefix traversals with exact zipped-slice traversals was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; split checksums and hierarchy metadata were unchanged.
- Geometric split / hierarchy-build ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst split / hierarchy / peak-RSS ratios: `{result.get("worst_split_time_ratio", 1.0):.3f}x` / `{result.get("worst_hierarchy_time_ratio", 1.0):.3f}x` / `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/zipped-ancestor-updates-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Zipped ancestor-update checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Re-profile the diameter pass after the zipped-update decision.\n"
        "2. Continue exact-preserving diameter-loop optimization from the updated profile.\n"
        "3. Refresh cumulative retained optimization and memory guidance.\n"
        "4. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Zipped ancestor-update gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Split / hierarchy ratios: `{split_ratio:.3f}x` / `{hierarchy_ratio:.3f}x`.
- Worst peak-RSS ratio: `{result.get("worst_peak_rss_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/zipped-ancestor-updates-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Zipped ancestor-update gate\n"
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
    "experiment": "zipped-ancestor-updates",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "split_cases": {},
    "hierarchy_cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-zipped-ancestor-baseline"))
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

    candidate = build(Path("/tmp/cmg-zipped-ancestor-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "4"]),
    )
    for name, arguments in specs:
        result["split_cases"][name] = compare("split", baseline, candidate, arguments, name)
        result["hierarchy_cases"][name] = compare("hierarchy", baseline, candidate, arguments, name)

    split_ratios = [case["candidate_over_baseline_time"] for case in result["split_cases"].values()]
    hierarchy_ratios = [case["candidate_over_baseline_time"] for case in result["hierarchy_cases"].values()]
    rss_ratios = [
        case["candidate_over_baseline_peak_rss"]
        for collection in (result["split_cases"], result["hierarchy_cases"])
        for case in collection.values()
    ]
    result["split_geometric_time_ratio"] = geometric(split_ratios)
    result["hierarchy_geometric_time_ratio"] = geometric(hierarchy_ratios)
    result["worst_split_time_ratio"] = max(split_ratios)
    result["worst_hierarchy_time_ratio"] = max(hierarchy_ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_split_case_count"] = sum(value < 1.0 for value in split_ratios)
    result["acceptance_limits"] = {
        "split_geometric_time_ratio_max": 0.99,
        "hierarchy_geometric_time_ratio_max": 0.998,
        "worst_split_time_ratio_max": 1.025,
        "worst_hierarchy_time_ratio_max": 1.025,
        "worst_peak_rss_ratio_max": 1.02,
        "improved_split_case_count_min": 3,
    }
    result["accepted"] = (
        result["split_geometric_time_ratio"] <= 0.99
        and result["hierarchy_geometric_time_ratio"] <= 0.998
        and result["worst_split_time_ratio"] <= 1.025
        and result["worst_hierarchy_time_ratio"] <= 1.025
        and result["worst_peak_rss_ratio"] <= 1.02
        and result["improved_split_case_count"] >= 3
    )
    result["decision_reason"] = (
        "full qualification passed; paired walk and ancestor-prefix updates use one exact bounds-coupled traversal"
        if result["accepted"]
        else "correctness passed, but split, hierarchy, or memory gates were not all met"
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
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
message = (
    "perf: retain zipped ancestor updates"
    if result.get("accepted", False)
    else "perf: record zipped ancestor-update experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push zipped ancestor-update decision")

if result.get("validation") != "success":
    raise SystemExit(1)
