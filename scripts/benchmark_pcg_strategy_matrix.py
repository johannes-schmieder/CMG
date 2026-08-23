"""Build and run a benchmark-only PCG parallel-strategy matrix."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path.cwd()
SOURCE = ROOT / "benchmarks/src/bin/pcg-strategy-matrix.rs"
RESULT = ROOT / ".ci/performance/pcg-strategy-matrix.json"
WORKFLOW = ROOT / ".github/workflows/pcg-strategy-matrix.yml"
SCRIPT = ROOT / "scripts/benchmark_pcg_strategy_matrix.py"
PLAN = ROOT / "PERFORMANCE_PLAN.md"

RUST_SOURCE = r'''use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor,
    ParallelOptions, PcgOptions, PcgResult, PcgWorkspace, solve_pcg_batch_with_executor,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

#[derive(Clone, Copy)]
enum Strategy {
    Serial,
    AcrossRhs,
    Planned,
}

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
}

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 0.5 + (vertex % 31) as f64 / 17.0))
        .collect();
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        vertices,
        edges: edge_count,
    }
}

fn worker_firm_graph(per_side: usize, degree: usize) -> BenchGraph {
    assert!(degree >= 2);
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
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        vertices,
        edges: edge_count,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!("unknown case {case}; expected path, worker-firm, or dense-worker-firm"),
    }
}

fn compatible_rhs(graph: &Laplacian, rhs_index: usize) -> Vec<f64> {
    let mut target: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let first = ((vertex * 17 + rhs_index * 31) % 257) as f64 - 128.0;
            let second = ((vertex * 43 + rhs_index * 19) % 101) as f64 - 50.0;
            first / 37.0 + second / 113.0
        })
        .collect();
    let mean = target.iter().sum::<f64>() / target.len().max(1) as f64;
    for value in &mut target {
        *value -= mean;
    }
    graph.matvec(&target).expect("known compatible rhs")
}

fn max_scaled_difference(left: &[f64], right: &[f64]) -> f64 {
    left.iter()
        .zip(right)
        .map(|(left, right)| {
            let scale = 1.0_f64.max(left.abs()).max(right.abs());
            (left - right).abs() / scale
        })
        .fold(0.0, f64::max)
}

fn validate_results(reference: &[PcgResult], candidate: &[PcgResult]) -> f64 {
    assert_eq!(reference.len(), candidate.len());
    reference
        .iter()
        .zip(candidate)
        .map(|(reference, candidate)| {
            assert_eq!(reference.iterations(), candidate.iterations());
            assert_eq!(reference.restarts(), candidate.restarts());
            assert_eq!(reference.residual_norm(), candidate.residual_norm());
            assert_eq!(reference.backward_error(), candidate.backward_error());
            max_scaled_difference(reference.solution(), candidate.solution())
        })
        .fold(0.0, f64::max)
}

#[allow(clippy::too_many_arguments)]
fn run_strategy(
    strategy: Strategy,
    graph: &Laplacian,
    preconditioner: &CmgPreconditioner,
    plan: &ParallelCmgPlan,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
    serial_workspace: &mut PcgWorkspace,
    planned_workspace: &mut PcgWorkspace,
    executor: &ParallelExecutor,
) -> Vec<PcgResult> {
    match strategy {
        Strategy::Serial => right_hand_sides
            .iter()
            .map(|rhs| {
                solve_pcg_with_workspace(
                    graph,
                    preconditioner,
                    rhs,
                    options,
                    serial_workspace,
                )
                .expect("serial solve should converge")
            })
            .collect(),
        Strategy::AcrossRhs => solve_pcg_batch_with_executor(
            graph,
            preconditioner,
            right_hand_sides,
            options,
            executor,
        )
        .expect("across-RHS solves should converge"),
        Strategy::Planned => right_hand_sides
            .iter()
            .map(|rhs| {
                solve_pcg_with_plan_and_workspace(
                    graph,
                    preconditioner,
                    plan,
                    rhs,
                    options,
                    planned_workspace,
                    executor,
                )
                .expect("planned solve should converge")
            })
            .collect(),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(50_000);
    let rhs_count = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("RHS count must be an integer"))
        .unwrap_or(4)
        .max(1);
    let repetitions = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("repetitions must be an integer"))
        .unwrap_or(2)
        .max(1);
    let threads = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("threads must be an integer"))
        .unwrap_or(4)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let right_hand_sides: Vec<Vec<f64>> = (0..rhs_count)
        .map(|index| compatible_rhs(&bench_graph.graph, index))
        .collect();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })
    .expect("parallel executor should build");
    let preconditioner = CmgPreconditioner::build(
        &bench_graph.graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
    )
    .expect("CMG preconditioner should build");
    let plan_start = Instant::now();
    let plan =
        ParallelCmgPlan::build(&preconditioner, &executor).expect("parallel plan should build");
    let plan_build_ns = plan_start.elapsed().as_nanos();
    let options = PcgOptions::default();
    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let mut planned_workspace = PcgWorkspace::new(&preconditioner);

    let serial_reference = run_strategy(
        Strategy::Serial,
        &bench_graph.graph,
        &preconditioner,
        &plan,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
        &executor,
    );
    let across_reference = run_strategy(
        Strategy::AcrossRhs,
        &bench_graph.graph,
        &preconditioner,
        &plan,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
        &executor,
    );
    let planned_reference = run_strategy(
        Strategy::Planned,
        &bench_graph.graph,
        &preconditioner,
        &plan,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
        &executor,
    );
    let across_difference = validate_results(&serial_reference, &across_reference);
    let planned_difference = validate_results(&serial_reference, &planned_reference);
    assert!(across_difference <= 5.0e-9);
    assert!(planned_difference <= 5.0e-9);

    let orders = [
        [Strategy::Serial, Strategy::AcrossRhs, Strategy::Planned],
        [Strategy::Planned, Strategy::AcrossRhs, Strategy::Serial],
        [Strategy::AcrossRhs, Strategy::Serial, Strategy::Planned],
    ];
    let mut serial_times = Vec::with_capacity(repetitions);
    let mut across_times = Vec::with_capacity(repetitions);
    let mut planned_times = Vec::with_capacity(repetitions);
    for repetition in 0..repetitions {
        for strategy in orders[repetition % orders.len()] {
            let start = Instant::now();
            let results = run_strategy(
                strategy,
                &bench_graph.graph,
                &preconditioner,
                &plan,
                black_box(&right_hand_sides),
                options,
                &mut serial_workspace,
                &mut planned_workspace,
                &executor,
            );
            let elapsed = start.elapsed().as_nanos();
            black_box(results);
            match strategy {
                Strategy::Serial => serial_times.push(elapsed),
                Strategy::AcrossRhs => across_times.push(elapsed),
                Strategy::Planned => planned_times.push(elapsed),
            }
        }
    }

    let serial_ns = median(serial_times);
    let across_ns = median(across_times);
    let planned_ns = median(planned_times);
    let workspace_bytes = serial_workspace.byte_len();
    let concurrency = executor
        .batch_concurrency(workspace_bytes, rhs_count)
        .expect("batch concurrency should be available");
    let auto_strategy = if rhs_count == 1 {
        if plan.operator_count() > 0 {
            "planned"
        } else {
            "serial"
        }
    } else if concurrency >= 2 {
        "across_rhs"
    } else if plan.operator_count() > 0 {
        "planned"
    } else {
        "serial"
    };
    let auto_ns = match auto_strategy {
        "planned" => planned_ns,
        "across_rhs" => across_ns,
        _ => serial_ns,
    };
    let best_ns = serial_ns.min(across_ns).min(planned_ns);

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"rhs_count\":{rhs_count},\"threads\":{},\"levels\":{},\"operators\":{},\"repetitions\":{repetitions},\"serial_ns\":{serial_ns},\"across_rhs_ns\":{across_ns},\"planned_ns\":{planned_ns},\"best_ns\":{best_ns},\"auto_strategy\":\"{auto_strategy}\",\"auto_ns\":{auto_ns},\"auto_over_best\":{:.17e},\"serial_over_best\":{:.17e},\"across_over_best\":{:.17e},\"planned_over_best\":{:.17e},\"concurrency\":{concurrency},\"plan_build_ns\":{plan_build_ns},\"plan_bytes\":{},\"workspace_bytes\":{workspace_bytes},\"across_workspace_bytes\":{},\"max_across_difference\":{across_difference:.17e},\"max_planned_difference\":{planned_difference:.17e}}}",
        bench_graph.vertices,
        bench_graph.edges,
        executor.thread_count(),
        preconditioner.hierarchy().levels().len(),
        plan.operator_count(),
        auto_ns as f64 / best_ns as f64,
        serial_ns as f64 / best_ns as f64,
        across_ns as f64 / best_ns as f64,
        planned_ns as f64 / best_ns as f64,
        plan.byte_len(),
        workspace_bytes.saturating_mul(concurrency),
    );
}
'''


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


result: dict[str, Any] = {
    "schema_version": 1,
    "experiment": "pcg-parallel-strategy-matrix",
    "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "status": "not_run",
    "cases": {},
}
try:
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE.write_text(RUST_SOURCE)
    run(["cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all"])
    run([
        "cargo", "fmt", "--manifest-path", "benchmarks/Cargo.toml", "--all", "--", "--check"
    ])
    run([
        "cargo", "clippy", "--manifest-path", "benchmarks/Cargo.toml", "--all-targets", "--", "-D", "warnings"
    ])
    run([
        "cargo", "build", "--manifest-path", "benchmarks/Cargo.toml", "--bin", "pcg-strategy-matrix", "--release"
    ])
    binary = ROOT / "benchmarks/target/release/pcg-strategy-matrix"
    specs: list[tuple[str, list[Any]]] = []
    for rhs_count in (1, 2, 4, 8):
        specs.extend(
            [
                (f"path-100k-rhs{rhs_count}-t4", ["path", 100_000, rhs_count, 2, 4]),
                (f"worker-firm-150k-rhs{rhs_count}-t4", ["worker-firm", 50_000, rhs_count, 2, 4]),
                (f"worker-firm-300k-rhs{rhs_count}-t4", ["worker-firm", 100_000, rhs_count, 2, 4]),
                (f"dense-worker-firm-400k-rhs{rhs_count}-t4", ["dense-worker-firm", 25_000, rhs_count, 2, 4]),
            ]
        )
    for rhs_count in (1, 2, 4):
        specs.extend(
            [
                (f"worker-firm-300k-rhs{rhs_count}-t2", ["worker-firm", 100_000, rhs_count, 2, 2]),
                (f"dense-worker-firm-400k-rhs{rhs_count}-t2", ["dense-worker-firm", 25_000, rhs_count, 2, 2]),
            ]
        )

    auto_ratios: list[float] = []
    maximum_difference = 0.0
    for name, arguments in specs:
        completed = run([str(binary), *[str(value) for value in arguments]])
        payloads = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip().startswith("{")
        ]
        if len(payloads) != 1:
            raise RuntimeError(f"unexpected benchmark output for {name}: {payloads}")
        payload = payloads[0]
        result["cases"][name] = payload
        auto_ratios.append(payload["auto_over_best"])
        maximum_difference = max(
            maximum_difference,
            payload["max_across_difference"],
            payload["max_planned_difference"],
        )
    result["status"] = "success"
    result["maximum_auto_over_best"] = max(auto_ratios)
    result["geometric_auto_over_best"] = math.exp(
        sum(math.log(value) for value in auto_ratios) / len(auto_ratios)
    )
    result["maximum_scaled_difference"] = maximum_difference
except Exception as error:
    result["status"] = "failure"
    result["error"] = repr(error)
    SOURCE.unlink(missing_ok=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "benchmarks/Cargo.lock"], check=False)
    print(f"strategy matrix failed: {error}", flush=True)

RESULT.parent.mkdir(parents=True, exist_ok=True)
RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

if PLAN.exists():
    text = PLAN.read_text()
    marker = "## Current next action\n"
    checkpoint = f'''### PCG strategy-matrix checkpoint — 2026-08-23

- Benchmark-only strategy matrix status: `{result["status"]}`.
- Cases compare serial sequential, across-RHS parallel, and planned within-solve PCG.
- The simple provisional auto policy had geometric/worst auto-to-best ratios of
  `{result.get("geometric_auto_over_best", float("nan")):.3f}x` and
  `{result.get("maximum_auto_over_best", float("nan")):.3f}x`.
- No production routing was changed by this checkpoint.
- Machine-readable evidence:
  `.ci/performance/pcg-strategy-matrix.json`.

'''
    if marker not in text:
        raise RuntimeError("PERFORMANCE_PLAN current-next-action heading missing")
    if "### PCG strategy-matrix checkpoint — 2026-08-23" not in text:
        text = text.replace(marker, checkpoint + marker, 1)
    PLAN.write_text(text)

WORKFLOW.unlink(missing_ok=True)
SCRIPT.unlink(missing_ok=True)
run(["git", "config", "user.name", "github-actions[bot]"])
run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
run(["git", "add", "-A"])
run(["git", "commit", "-m", "perf: record PCG parallel strategy matrix"])
run(["git", "pull", "--rebase", "origin", "main"])
run(["git", "push", "origin", "HEAD:main"])
