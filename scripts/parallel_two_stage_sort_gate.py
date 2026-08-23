import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/graph.rs")
TEMP_BENCH = Path("src/bin/parallel-sort-gate.rs")
WORKFLOW = Path(".github/workflows/parallel-two-stage-sort.yml")
SCRIPT = Path("scripts/parallel_two_stage_sort_gate.py")
RECORD = Path(".ci/performance/parallel-two-stage-sort-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCHMARK_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{CmgOptions, CmgPreconditioner, Laplacian, ParallelExecutor, ParallelOptions};

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

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap();
    let scale = arguments.next().unwrap().parse::<usize>().unwrap();
    let repetitions = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let threads = arguments.next().unwrap().parse::<usize>().unwrap().max(1);
    let graph = match case.as_str() {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case"),
    };
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();

    let warmup = CmgPreconditioner::build_with_executor(
        black_box(&graph),
        CmgOptions::default(),
        &executor,
    )
    .unwrap();
    let vertex_counts = warmup.hierarchy().report().vertex_counts().to_vec();
    let matrix_nonzeros = warmup.hierarchy().report().matrix_nonzeros().to_vec();
    black_box(warmup);

    let mut elapsed = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let preconditioner = CmgPreconditioner::build_with_executor(
            black_box(&graph),
            CmgOptions::default(),
            &executor,
        )
        .unwrap();
        elapsed.push(start.elapsed().as_nanos());
        assert_eq!(preconditioner.hierarchy().report().vertex_counts(), vertex_counts);
        assert_eq!(preconditioner.hierarchy().report().matrix_nonzeros(), matrix_nonzeros);
        black_box(preconditioner);
    }

    println!(
        "{{\"case\":\"{}\",\"scale\":{},\"vertices\":{},\"edges\":{},\"threads\":{},\"repetitions\":{},\"median_ns\":{},\"vertex_counts\":{:?},\"matrix_nonzeros\":{:?}}}",
        case,
        scale,
        graph.vertex_count(),
        graph.edge_count(),
        threads,
        repetitions,
        median(elapsed),
        vertex_counts,
        matrix_nonzeros,
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
            f"command failed ({completed.returncode}): {' '.join(str(item) for item in command)}"
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
            "--features",
            "parallel",
            "--bin",
            "parallel-sort-gate",
        ],
        env=env,
    )
    return target / "release" / "parallel-sort-gate"


def sample(binary, arguments, tag):
    time_path = Path(f"/tmp/cmg-par-sort-{tag}.time")
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
        raise RuntimeError(f"unexpected parallel setup output: {payloads}")
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
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline),
            ("candidate", candidate),
            ("candidate", candidate),
            ("baseline", baseline),
        )
    ):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(observation)

    stable = (
        "case",
        "scale",
        "vertices",
        "edges",
        "threads",
        "repetitions",
        "vertex_counts",
        "matrix_nonzeros",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: parallel hierarchy changed {key}")

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


OLD_PARALLEL = "            executor.install(|| raw.par_sort_unstable_by(compare_raw_edges));\n"
NEW_PARALLEL = (
    "            executor.install(|| raw.par_sort_unstable_by_key(endpoint_key));\n"
    "            sort_duplicate_edge_weights(&mut raw);\n"
)
OLD_HELPER = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}
'''
NEW_HELPER = '''fn sort_compact_edges_two_stage(raw: &mut [Edge]) {
    raw.sort_unstable_by_key(endpoint_key);
    sort_duplicate_edge_weights(raw);
}

fn sort_duplicate_edge_weights(raw: &mut [Edge]) {
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        if end - start > 1 {
            raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        }
        start = end;
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_PARALLEL) != 1:
        raise RuntimeError("parallel compact sort site changed unexpectedly")
    if source.count(OLD_HELPER) != 1:
        raise RuntimeError("serial two-stage helper changed unexpectedly")
    return source.replace(OLD_PARALLEL, NEW_PARALLEL, 1).replace(OLD_HELPER, NEW_HELPER, 1)


def update_documents(result):
    accepted = result.get("accepted", False)
    decision = "retained" if accepted else "not retained"
    geometric = result.get("active_geometric_time_ratio", 1.0)
    worst = result.get("worst_time_ratio", 1.0)
    rss = result.get("worst_peak_rss_ratio", 1.0)
    checkpoint = f'''### Parallel endpoint-first sort checkpoint — 2026-08-23

- Parallel endpoint-first compact-edge ordering was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Active worker/dense geometric setup ratio: `{geometric:.3f}x`.
- Worst setup ratio: `{worst:.3f}x`; worst peak-RSS ratio: `{rss:.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/parallel-two-stage-sort-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### Parallel endpoint-first sort checkpoint — 2026-08-23\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## Parallel endpoint-first sort gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Worker/dense geometric ratio: `{geometric:.3f}x`.
- Worst setup / peak-RSS ratios: `{worst:.3f}x` / `{rss:.3f}x`.
- Evidence: `.ci/performance/parallel-two-stage-sort-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## Parallel endpoint-first sort gate\n"
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
TEMP_BENCH.write_text(BENCHMARK_SOURCE)
result = {
    "schema_version": 1,
    "experiment": "parallel-endpoint-first-weight-sort",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-par-sort-baseline"))
    SOURCE.write_text(apply_candidate(baseline_source))

    run(["cargo", "fmt", "--all"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"])
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

    candidate = build(Path("/tmp/cmg-par-sort-candidate"))
    specs = (
        ("path-1m", ["path", "1000000", "2", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "2", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "2", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "2", "4"]),
        ("dense-worker-firm-3.2m", ["dense-worker-firm", "200000", "2", "4"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(baseline, candidate, arguments, name)

    ratios = [case["candidate_over_baseline_time"] for case in result["cases"].values()]
    active_names = (
        "worker-firm-1.5m",
        "worker-firm-3m",
        "dense-worker-firm-1.6m",
        "dense-worker-firm-3.2m",
    )
    active_ratios = [result["cases"][name]["candidate_over_baseline_time"] for name in active_names]
    rss_ratios = [case["candidate_over_baseline_peak_rss"] for case in result["cases"].values()]
    result["active_geometric_time_ratio"] = math.exp(
        sum(math.log(value) for value in active_ratios) / len(active_ratios)
    )
    result["worst_time_ratio"] = max(ratios)
    result["worst_peak_rss_ratio"] = max(rss_ratios)
    result["improved_active_case_count"] = sum(value < 1.0 for value in active_ratios)
    result["acceptance_limits"] = {
        "active_geometric_time_ratio_max": 0.985,
        "improved_active_case_count_min": 3,
        "worst_time_ratio_max": 1.04,
        "worst_peak_rss_ratio_max": 1.03,
    }
    result["accepted"] = (
        result["active_geometric_time_ratio"] <= 0.985
        and result["improved_active_case_count"] >= 3
        and result["worst_time_ratio"] <= 1.04
        and result["worst_peak_rss_ratio"] <= 1.03
    )
    result["decision_reason"] = (
        "full qualification passed; endpoint-first parallel sorting improved large worker-firm hierarchy setup"
        if result["accepted"]
        else "qualification passed, but parallel setup timing or memory gates were not met"
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
result.setdefault("active_geometric_time_ratio", 1.0)
result.setdefault("worst_time_ratio", 1.0)
result.setdefault("worst_peak_rss_ratio", 1.0)
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
    "perf: retain parallel endpoint-first sorting"
    if result.get("accepted", False)
    else "perf: record parallel endpoint-first sort experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push parallel endpoint-first sort decision")
