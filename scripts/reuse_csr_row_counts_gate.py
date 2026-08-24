import json
import math
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path.cwd()
SOURCE = Path("src/csr.rs")
TEMP_BENCH = Path("src/bin/csr-plan-build-gate.rs")
WORKFLOW = Path(".github/workflows/reuse-csr-row-counts.yml")
SCRIPT = Path("scripts/reuse_csr_row_counts_gate.py")
RECORD = Path(".ci/performance/reuse-csr-row-counts-latest.json")
PLAN = Path("PERFORMANCE_PLAN.md")
STATUS = Path("PERFORMANCE_STATUS.md")

BENCHMARK_SOURCE = r'''use std::alloc::{GlobalAlloc, Layout, System};
use std::hint::black_box;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions,
};

struct CountingAllocator {
    current: AtomicUsize,
    peak: AtomicUsize,
}

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            let current = self.current.fetch_add(layout.size(), Ordering::SeqCst) + layout.size();
            self.peak.fetch_max(current, Ordering::SeqCst);
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        unsafe { System.dealloc(pointer, layout) };
        self.current.fetch_sub(layout.size(), Ordering::SeqCst);
    }

    unsafe fn realloc(&self, pointer: *mut u8, old: Layout, new_size: usize) -> *mut u8 {
        let new_pointer = unsafe { System.realloc(pointer, old, new_size) };
        if !new_pointer.is_null() {
            if new_size >= old.size() {
                let increase = new_size - old.size();
                let current = self.current.fetch_add(increase, Ordering::SeqCst) + increase;
                self.peak.fetch_max(current, Ordering::SeqCst);
            } else {
                self.current.fetch_sub(old.size() - new_size, Ordering::SeqCst);
            }
        }
        new_pointer
    }
}

impl CountingAllocator {
    fn current(&self) -> usize {
        self.current.load(Ordering::SeqCst)
    }

    fn reset_peak(&self) {
        self.peak.store(self.current(), Ordering::SeqCst);
    }

    fn peak(&self) -> usize {
        self.peak.load(Ordering::SeqCst)
    }
}

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator {
    current: AtomicUsize::new(0),
    peak: AtomicUsize::new(0),
};

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
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
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .unwrap_or_else(|| "200000".to_owned())
        .parse::<usize>()
        .unwrap();
    let repetitions = arguments
        .next()
        .unwrap_or_else(|| "5".to_owned())
        .parse::<usize>()
        .unwrap()
        .max(1);
    let threads = arguments
        .next()
        .unwrap_or_else(|| "4".to_owned())
        .parse::<usize>()
        .unwrap()
        .max(1);
    let degree = match case.as_str() {
        "worker-firm" => 3,
        "dense-worker-firm" => 16,
        _ => panic!("unknown case"),
    };
    let graph = worker_firm_graph(scale, degree);
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        ..ParallelOptions::default()
    })
    .unwrap();
    let preconditioner = CmgPreconditioner::build_with_executor(
        &graph,
        CmgOptions::default(),
        &executor,
    )
    .unwrap();

    let warmup = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    let operator_count = warmup.operator_count();
    let plan_bytes = warmup.byte_len();
    drop(warmup);

    let mut elapsed = Vec::with_capacity(repetitions);
    let mut additional_peak = Vec::with_capacity(repetitions);
    let mut retained = Vec::with_capacity(repetitions);
    let mut post_drop_delta = 0_usize;
    for _ in 0..repetitions {
        let before = ALLOCATOR.current();
        ALLOCATOR.reset_peak();
        let started = Instant::now();
        let plan = ParallelCmgPlan::build(black_box(&preconditioner), &executor).unwrap();
        elapsed.push(started.elapsed().as_nanos());
        assert_eq!(plan.operator_count(), operator_count);
        assert_eq!(plan.byte_len(), plan_bytes);
        let after = ALLOCATOR.current();
        additional_peak.push(ALLOCATOR.peak().saturating_sub(before));
        retained.push(after.saturating_sub(before));
        black_box(&plan);
        drop(plan);
        post_drop_delta = post_drop_delta.max(ALLOCATOR.current().abs_diff(before));
    }

    println!(
        "{{\"case\":\"{}\",\"scale\":{},\"vertices\":{},\"edges\":{},\"levels\":{},\"operators\":{},\"plan_bytes\":{},\"threads\":{},\"repetitions\":{},\"median_ns\":{},\"median_additional_peak_bytes\":{},\"median_retained_bytes\":{},\"max_post_drop_delta_bytes\":{}}}",
        case,
        scale,
        graph.vertex_count(),
        graph.edge_count(),
        preconditioner.hierarchy().levels().len(),
        operator_count,
        plan_bytes,
        threads,
        repetitions,
        median(elapsed),
        median(additional_peak),
        median(retained),
        post_drop_delta,
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
            "--features",
            "parallel",
            "--bin",
            "csr-plan-build-gate",
        ],
        env=env,
    )
    return target / "release" / "csr-plan-build-gate"


def sample(binary, arguments, tag):
    completed = run([binary, *arguments])
    payloads = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"unexpected plan-build output: {payloads}")
    return payloads[0]


def compare_case(baseline, candidate, arguments, name):
    baseline_samples = []
    candidate_samples = []
    for index, (label, binary) in enumerate(
        (
            ("baseline", baseline),
            ("candidate", candidate),
            ("candidate", candidate),
            ("baseline", baseline),
            ("candidate", candidate),
            ("baseline", baseline),
        )
    ):
        observation = sample(binary, arguments, f"{name}-{label}-{index}")
        (baseline_samples if label == "baseline" else candidate_samples).append(
            observation
        )

    stable = (
        "case",
        "scale",
        "vertices",
        "edges",
        "levels",
        "operators",
        "plan_bytes",
        "threads",
        "repetitions",
        "max_post_drop_delta_bytes",
    )
    reference = baseline_samples[0]
    for observation in baseline_samples[1:] + candidate_samples:
        for key in stable:
            if observation[key] != reference[key]:
                raise RuntimeError(f"{name}: unstable plan metadata for {key}")

    result = {
        "arguments": arguments,
        "metadata": {key: reference[key] for key in stable},
    }
    for field in (
        "median_ns",
        "median_additional_peak_bytes",
        "median_retained_bytes",
    ):
        baseline_value = statistics.median(item[field] for item in baseline_samples)
        candidate_value = statistics.median(item[field] for item in candidate_samples)
        result[f"baseline_{field}"] = baseline_value
        result[f"candidate_{field}"] = candidate_value
        result[f"candidate_over_baseline_{field}"] = candidate_value / baseline_value
    return result


OLD_PREFIX = '''        let mut row_offsets = Vec::with_capacity(vertex_count + 1);
        row_offsets.push(0);
        for count in row_counts {
'''
NEW_PREFIX = '''        let mut row_offsets = Vec::with_capacity(vertex_count + 1);
        row_offsets.push(0);
        for &count in &row_counts {
'''
OLD_NEXT = '''        let mut next = row_offsets[..vertex_count].to_vec();
'''
NEW_NEXT = '''        row_counts.copy_from_slice(&row_offsets[..vertex_count]);
        let mut next = row_counts;
'''
TEST_MODULE = '''

#[cfg(test)]
mod reused_row_count_tests {
    use super::CsrLaplacian;
    use crate::Laplacian;

    #[test]
    fn reused_row_counts_preserve_canonical_rows() {
        let graph = Laplacian::from_edges(
            6,
            [
                (0, 1, 1.0),
                (0, 2, 2.0),
                (1, 3, 3.0),
                (2, 3, 4.0),
                (3, 4, 5.0),
                (4, 5, 6.0),
            ],
        )
        .unwrap();
        let csr = CsrLaplacian::from_laplacian(&graph).unwrap();
        let input = [1.0, -2.0, 0.5, 3.0, -1.0, 2.0];
        let expected = graph.matvec(&input).unwrap();
        assert_eq!(csr.matvec(&input).unwrap(), expected);
    }
}
'''


def apply_candidate(source):
    if source.count(OLD_PREFIX) != 1:
        raise RuntimeError("CSR row-offset construction marker changed unexpectedly")
    if source.count(OLD_NEXT) != 1:
        raise RuntimeError("CSR insertion-cursor marker changed unexpectedly")
    candidate = source.replace(OLD_PREFIX, NEW_PREFIX, 1)
    candidate = candidate.replace(OLD_NEXT, NEW_NEXT, 1)
    if "mod reused_row_count_tests" not in candidate:
        candidate += TEST_MODULE
    return candidate


def geometric(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def update_documents(result):
    decision = "retained" if result.get("accepted", False) else "not retained"
    time_ratio = result.get("geometric_time_ratio", 1.0)
    peak_ratio = result.get("geometric_additional_peak_ratio", 1.0)
    checkpoint = f'''### CSR row-count reuse checkpoint — 2026-08-24

- Reusing row counts as CSR insertion cursors was **{decision}**.
- Validation: `{result.get("validation", "unknown")}`.
- Geometric plan-build time ratio: `{time_ratio:.3f}x`.
- Exact additional-peak ratio: `{peak_ratio:.3f}x`; retained plan ratio: `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Decision: {result.get("decision_reason", "missing")}.
- Evidence: `.ci/performance/reuse-csr-row-counts-latest.json`.

'''
    plan = PLAN.read_text()
    marker = "## Current next action\n"
    heading = "### CSR row-count reuse checkpoint — 2026-08-24\n"
    if heading in plan:
        start = plan.index(heading)
        end = plan.index(marker, start)
        plan = plan[:start] + checkpoint + plan[end:]
    elif marker in plan:
        plan = plan.replace(marker, checkpoint + marker, 1)
    else:
        plan += "\n\n" + checkpoint
    PLAN.write_text(plan)

    block = f'''## CSR row-count reuse gate

- Decision: `{decision}`.
- Validation: `{result.get("validation", "unknown")}`.
- Plan-build time / exact peak ratios: `{time_ratio:.3f}x` / `{peak_ratio:.3f}x`.
- Retained plan ratio: `{result.get("geometric_retained_ratio", 1.0):.3f}x`.
- Evidence: `.ci/performance/reuse-csr-row-counts-latest.json`.
'''
    status = STATUS.read_text().rstrip()
    heading = "## CSR row-count reuse gate\n"
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
    "experiment": "reuse-csr-row-counts",
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "accepted": False,
    "validation": "not_run",
    "cases": {},
}

try:
    run(["cargo", "fmt", "--all"])
    baseline = build(Path("/tmp/cmg-csr-row-baseline"))
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
            "D",
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

    candidate = build(Path("/tmp/cmg-csr-row-candidate"))
    specs = (
        ("worker-firm-600k", ["worker-firm", "200000", "5", "4"]),
        ("worker-firm-1.5m", ["worker-firm", "500000", "5", "4"]),
        ("worker-firm-3m", ["worker-firm", "1000000", "5", "4"]),
        ("dense-worker-firm-800k", ["dense-worker-firm", "50000", "5", "4"]),
        ("dense-worker-firm-1.6m", ["dense-worker-firm", "100000", "5", "4"]),
    )
    for name, arguments in specs:
        result["cases"][name] = compare_case(
            baseline, candidate, arguments, name
        )

    time_ratios = [
        case["candidate_over_baseline_median_ns"]
        for case in result["cases"].values()
    ]
    peak_ratios = [
        case["candidate_over_baseline_median_additional_peak_bytes"]
        for case in result["cases"].values()
    ]
    retained_ratios = [
        case["candidate_over_baseline_median_retained_bytes"]
        for case in result["cases"].values()
    ]
    result["geometric_time_ratio"] = geometric(time_ratios)
    result["worst_time_ratio"] = max(time_ratios)
    result["geometric_additional_peak_ratio"] = geometric(peak_ratios)
    result["worst_additional_peak_ratio"] = max(peak_ratios)
    result["geometric_retained_ratio"] = geometric(retained_ratios)
    result["worst_retained_ratio"] = max(retained_ratios)
    result["max_post_drop_delta_bytes"] = max(
        case["metadata"]["max_post_drop_delta_bytes"]
        for case in result["cases"].values()
    )
    result["acceptance_limits"] = {
        "geometric_time_ratio_max": 1.01,
        "worst_time_ratio_max": 1.04,
        "geometric_additional_peak_ratio_max": 0.95,
        "worst_additional_peak_ratio_max": 0.98,
        "geometric_retained_ratio_max": 1.001,
        "worst_retained_ratio_max": 1.001,
        "max_post_drop_delta_bytes": 0,
    }
    result["accepted"] = (
        result["geometric_time_ratio"] <= 1.01
        and result["worst_time_ratio"] <= 1.04
        and result["geometric_additional_peak_ratio"] <= 0.95
        and result["worst_additional_peak_ratio"] <= 0.98
        and result["geometric_retained_ratio"] <= 1.001
        and result["worst_retained_ratio"] <= 1.001
        and result["max_post_drop_delta_bytes"] == 0
    )
    result["decision_reason"] = (
        "full qualification passed; the CSR insertion cursor reuses the row-count allocation with lower plan-build peak memory"
        if result["accepted"]
        else "validation passed, but plan-build timing or exact-memory gates were not all met"
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
    "geometric_time_ratio",
    "worst_time_ratio",
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
    "perf: retain CSR row-count reuse"
    if result.get("accepted", False)
    else "perf: record CSR row-count reuse experiment"
)
run(["git", "commit", "-m", message])
for _ in range(5):
    run(["git", "pull", "--rebase", "origin", "main"])
    pushed = run(["git", "push", "origin", "HEAD:main"], check=False)
    if pushed.returncode == 0:
        break
else:
    raise RuntimeError("failed to push CSR row-count reuse decision")
