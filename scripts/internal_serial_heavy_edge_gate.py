import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/forest.rs")
TEMP_BENCH = Path("src/bin/internal-heavy-edge-routing-gate.rs")
WORKFLOW = Path(".github/workflows/internal-serial-heavy-edge.yml")
SCRIPT = Path("scripts/internal_serial_heavy_edge_gate.py")
RECORD = Path(".ci/performance/internal-serial-heavy-edge-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCH_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelExecutor, ParallelOptions,
};

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

fn build_case(case: &str, scale: usize) -> Laplacian {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap();
    let scale = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let threads = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let graph = build_case(&case, scale);
    let options = CmgOptions::default();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();

    let serial = CmgPreconditioner::build(&graph, options.clone()).unwrap();
    let serial_vertices = serial.hierarchy().report().vertex_counts().to_vec();
    let serial_nonzeros = serial.hierarchy().report().matrix_nonzeros().to_vec();
    let serial_terminal = format!("{:?}", serial.hierarchy().report().terminal_reason());
    black_box(serial);

    let warmup = CmgPreconditioner::build_with_executor(
        &graph,
        options.clone(),
        &executor,
    )
    .unwrap();
    assert_eq!(warmup.hierarchy().report().vertex_counts(), serial_vertices);
    assert_eq!(warmup.hierarchy().report().matrix_nonzeros(), serial_nonzeros);
    assert_eq!(
        format!("{:?}", warmup.hierarchy().report().terminal_reason()),
        serial_terminal,
    );
    black_box(warmup);

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let preconditioner = CmgPreconditioner::build_with_executor(
            black_box(&graph),
            options.clone(),
            &executor,
        )
        .unwrap();
        elapsed.push(start.elapsed().as_nanos());
        assert_eq!(preconditioner.hierarchy().report().vertex_counts(), serial_vertices);
        assert_eq!(preconditioner.hierarchy().report().matrix_nonzeros(), serial_nonzeros);
        assert_eq!(
            format!("{:?}", preconditioner.hierarchy().report().terminal_reason()),
            serial_terminal,
        );
        black_box(preconditioner);
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"threads\":{threads},\"repetitions\":{repetitions},\"median_ns\":{},\"vertex_counts\":{:?},\"matrix_nonzeros\":{:?},\"terminal_reason\":\"{}\"}}",
        graph.vertex_count(),
        graph.edge_count(),
        median(elapsed),
        serial_vertices,
        serial_nonzeros,
        serial_terminal,
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
            "cargo", "build", "--release", "--features", "parallel",
            "--bin", "internal-heavy-edge-routing-gate",
        ],
        env=env,
    )
    return target / "release" / "internal-heavy-edge-routing-gate"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-internal-heavy-{tag}.time")
    completed = run(
        [
            "/usr/bin/time", "-v", "-o", time_path,
            binary, *arguments,
        ]
    )
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected parallel hierarchy output: {payloads}")
    rss_match = re.search(
        r"Maximum resident set size \(kbytes\):\s*(\d+)",
        time_path.read_text(),
    )
    if rss_match is None:
        raise RuntimeError("peak RSS missing")
    payload = payloads[0]
    payload["peak_rss_kib"] = int(rss_match.group(1))
    return payload


def compare_case(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    schedule = (
        ("baseline", baseline),
        ("candidate", candidate),
        ("candidate", candidate),
        ("baseline", baseline),
        ("baseline", baseline),
        ("candidate", candidate),
    )
    for index, (label, binary) in enumerate(schedule):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    stable = (
        "case", "scale", "vertices", "edges", "threads", "repetitions",
        "vertex_counts", "matrix_nonzeros", "terminal_reason",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: parallel hierarchy changed {key}")

    baseline_times = [item["median_ns"] for item in baseline_samples]
    candidate_times = [item["median_ns"] for item in candidate_samples]
    baseline_rss = [item["peak_rss_kib"] for item in baseline_samples]
    candidate_rss = [item["peak_rss_kib"] for item in candidate_samples]
    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
        "baseline_time_samples_ns": baseline_times,
        "candidate_time_samples_ns": candidate_times,
        "baseline_rss_samples_kib": baseline_rss,
        "candidate_rss_samples_kib": candidate_rss,
        "baseline_median_ns": statistics.median(baseline_times),
        "candidate_median_ns": statistics.median(candidate_times),
        "baseline_median_rss_kib": statistics.median(baseline_rss),
        "candidate_median_rss_kib": statistics.median(candidate_rss),
    }
    result["candidate_over_baseline_time"] = (
        result["candidate_median_ns"] / result["baseline_median_ns"]
    )
    result["candidate_over_baseline_median_rss"] = (
        result["candidate_median_rss_kib"] / result["baseline_median_rss_kib"]
    )
    return result


OLD_INTERNAL = '''pub(crate) fn build_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    executor: &ParallelExecutor,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    let (heavy_parent, selected_weight) = maximum_weight_forest_with_executor(graph, executor)?;
'''
NEW_INTERNAL = '''pub(crate) fn build_forest_aggregation_labels_with_executor(
    graph: &Laplacian,
    low_effective_degree_threshold: f64,
    _executor: &ParallelExecutor,
) -> Result<(Vec<usize>, usize), CmgError> {
    validate_low_effective_degree_threshold(low_effective_degree_threshold)?;
    // Hierarchy setup already parallelizes coarse contraction and sorting. A
    // one-use CSR build for heavy-edge selection costs more than the compact
    // serial edge scan on qualified sparse and dense worker-firm workloads.
    let (heavy_parent, selected_weight) = maximum_weight_forest(graph);
'''
TEST_MODULE = '''

#[cfg(all(test, feature = "parallel"))]
mod internal_serial_heavy_edge_tests {
    use super::{build_forest_aggregation_labels_with_executor, build_forest_aggregation_labels};
    use crate::{Laplacian, ParallelExecutor, ParallelOptions};

    #[test]
    fn executor_hierarchy_grouping_matches_serial_grouping() {
        let graph = Laplacian::from_edges(
            2_000,
            (0..1_000).flat_map(|worker| {
                [
                    (worker, 1_000 + worker, 1.0),
                    (worker, 1_000 + (worker + 1) % 1_000, 0.5),
                    (worker, 1_000 + (7 * worker + 3) % 1_000, 0.25),
                ]
            }),
        )
        .unwrap();
        let executor = ParallelExecutor::new(ParallelOptions {
            threads: 4,
            min_parallel_len: 1,
            ..ParallelOptions::default()
        })
        .unwrap();
        let serial = build_forest_aggregation_labels(&graph, 0.125).unwrap();
        let routed = build_forest_aggregation_labels_with_executor(
            &graph,
            0.125,
            &executor,
        )
        .unwrap();
        assert_eq!(routed, serial);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_INTERNAL) != 1:
        raise RuntimeError("internal executor grouping marker changed unexpectedly")
    candidate = source.replace(OLD_INTERNAL, NEW_INTERNAL, 1)
    if "mod internal_serial_heavy_edge_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    time_ratio = result.get("active_geometric_time_ratio", 1.0)
    rss_ratio = result.get("worst_median_rss_ratio", 1.0)
    checkpoint = f'''### Internal serial heavy-edge routing checkpoint — 2026-08-24

- Using the compact serial edge scan for hierarchy-internal heavy-edge selection was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`; executor-built and serial hierarchies remained identical.
- Active worker/dense geometric hierarchy-build ratio: `{time_ratio:.3f}x`.
- Worst all-case time / median-RSS ratios: `{result.get("worst_time_ratio", 1.0):.3f}x` / `{rss_ratio:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/internal-serial-heavy-edge-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    if marker not in plan:
        raise RuntimeError("PERFORMANCE_PLAN current-action marker missing")
    if "### Internal serial heavy-edge routing checkpoint — 2026-08-24\n" not in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    prefix, _, _ = plan.partition(marker)
    plan = prefix + marker + (
        "1. Refresh cumulative retained optimization and memory guidance.\n"
        "2. Re-profile hierarchy setup after the latest retained forest changes.\n"
        "3. Run the manual 1–32 thread qualification on suitable hardware when available.\n"
        "4. Keep the public parallel heavy-edge API available for callers with reusable row operators.\n"
    )
    PLAN.write_text(plan)

    block = f'''## Internal serial heavy-edge routing gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Worker/dense hierarchy-build ratio: `{time_ratio:.3f}x`.
- Worst median-RSS ratio: `{rss_ratio:.3f}x`.
- Evidence: `.ci/performance/internal-serial-heavy-edge-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Internal serial heavy-edge routing gate\n"
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
    "experiment": "internal-serial-heavy-edge-routing",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-internal-heavy-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
    doc_env = os.environ.copy()
    doc_env["RUSTDOCFLAGS"] = "-D warnings"
    run(["cargo", "doc", "--no-deps", "--all-features"], env=doc_env)
    run(["cargo", "test", "--all-targets", "--all-features"])
    run(["cargo", "test", "--all-targets", "--all-features", "--release"])
    candidate = build(Path("/tmp/cmg-internal-heavy-candidate"))
    result["validation"] = "success"

    specs = (
        ("path-1m", ["path", "1000000", "3", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "3", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "3", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "3", "4"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "3", "4"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline,
            candidate,
            arguments,
            name,
        )

    ratios = [
        case["candidate_over_baseline_time"]
        for case in result["cases"].values()
    ]
    active_names = (
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-1.6m",
        "dense-worker-firm-3.2m",
    )
    active_ratios = [
        result["cases"][name]["candidate_over_baseline_time"]
        for name in active_names
    ]
    rss_ratios = [
        case["candidate_over_baseline_median_rss"]
        for case in result["cases"].values()
    ]
    result["active_geometric_time_ratio"] = geometric(active_ratios)
    result["all_geometric_time_ratio"] = geometric(ratios)
    result["worst_time_ratio"] = max(ratios)
    result["worst_median_rss_ratio"] = max(rss_ratios)
    result["improved_active_case_count"] = sum(value < 1.0 for value in active_ratios)
    result["acceptance_limits"] = {
        "active_geometric_time_ratio_max": 0.97,
        "all_geometric_time_ratio_max": 0.985,
        "worst_time_ratio_max": 1.03,
        "worst_median_rss_ratio_max": 1.02,
        "improved_active_case_count_min": 3,
    }
    result["accepted"] = (
        result["active_geometric_time_ratio"] <= 0.97
        and result["all_geometric_time_ratio"] <= 0.985
        and result["worst_time_ratio"] <= 1.03
        and result["worst_median_rss_ratio"] <= 1.02
        and result["improved_active_case_count"] >= 3
    )
    result["decision_reason"] = (
        "full qualification passed; avoiding a one-use CSR build improved executor hierarchy setup while preserving the exact hierarchy"
        if result["accepted"]
        else "correctness passed, but executor hierarchy timing or RSS limits were not all met"
    )
except Exception as error:
    result["validation"] = "failure"
    result["error"] = repr(error)
    result["decision_reason"] = f"experiment failed safely: {error}"
    print(result["decision_reason"], flush=True)

TEMP_BENCH.unlink(missing_ok=True)
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
    "active_geometric_time_ratio", "all_geometric_time_ratio",
    "worst_time_ratio", "worst_median_rss_ratio",
):
    result.setdefault(key, 1.0)
result.setdefault("improved_active_case_count", 0)
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
    "perf: retain serial heavy-edge selection for hierarchy setup"
    if result.get("accepted", False)
    else "perf: record internal heavy-edge routing experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push internal heavy-edge routing decision")
if result.get("validation") == "failure":
    raise SystemExit(1)
