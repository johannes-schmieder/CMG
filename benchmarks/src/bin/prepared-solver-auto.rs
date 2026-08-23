use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, Laplacian, ParallelOptions, ParallelPcgExecution, ParallelPcgPolicy,
    ParallelPcgSolver, PcgOptions, PcgResult, PcgWorkspace, solve_pcg_batch_with_executor,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

#[derive(Clone, Copy)]
enum ExplicitStrategy {
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
        _ => panic!("unknown case {case}"),
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
fn run_explicit(
    strategy: ExplicitStrategy,
    solver: &ParallelPcgSolver,
    right_hand_sides: &[Vec<f64>],
    options: PcgOptions,
    serial_workspace: &mut PcgWorkspace,
    planned_workspace: &mut PcgWorkspace,
) -> Vec<PcgResult> {
    match strategy {
        ExplicitStrategy::Serial => right_hand_sides
            .iter()
            .map(|rhs| {
                solve_pcg_with_workspace(
                    solver.graph(),
                    solver.preconditioner(),
                    rhs,
                    options,
                    serial_workspace,
                )
                .expect("serial solve should converge")
            })
            .collect(),
        ExplicitStrategy::AcrossRhs => solve_pcg_batch_with_executor(
            solver.graph(),
            solver.preconditioner(),
            right_hand_sides,
            options,
            solver.executor(),
        )
        .expect("across-RHS solves should converge"),
        ExplicitStrategy::Planned => right_hand_sides
            .iter()
            .map(|rhs| {
                solve_pcg_with_plan_and_workspace(
                    solver.graph(),
                    solver.preconditioner(),
                    solver.plan(),
                    rhs,
                    options,
                    planned_workspace,
                    solver.executor(),
                )
                .expect("planned solve should converge")
            })
            .collect(),
    }
}

fn execution_name(execution: ParallelPcgExecution) -> &'static str {
    match execution {
        ParallelPcgExecution::Serial => "serial",
        ParallelPcgExecution::Planned => "planned",
        ParallelPcgExecution::AcrossRightHandSides => "across_rhs",
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|value| value.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(50_000);
    let rhs_count = arguments
        .next()
        .map(|value| {
            value
                .parse::<usize>()
                .expect("RHS count must be an integer")
        })
        .unwrap_or(4)
        .max(1);
    let repetitions = arguments
        .next()
        .map(|value| {
            value
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(2)
        .max(1);
    let threads = arguments
        .next()
        .map(|value| value.parse::<usize>().expect("threads must be an integer"))
        .unwrap_or(4)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let right_hand_sides: Vec<Vec<f64>> = (0..rhs_count)
        .map(|index| compatible_rhs(&bench_graph.graph, index))
        .collect();
    let build_start = Instant::now();
    let solver = ParallelPcgSolver::build_with_policy(
        &bench_graph.graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
        ParallelOptions {
            threads,
            min_parallel_len: 16_384,
            ..ParallelOptions::default()
        },
        ParallelPcgPolicy::default(),
    )
    .expect("prepared solver should build");
    let build_ns = build_start.elapsed().as_nanos();
    let options = PcgOptions::default();
    let mut serial_workspace = PcgWorkspace::new(solver.preconditioner());
    let mut planned_workspace = PcgWorkspace::new(solver.preconditioner());
    let mut auto_workspace = solver.workspace();

    let serial_reference = run_explicit(
        ExplicitStrategy::Serial,
        &solver,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
    );
    let across_reference = run_explicit(
        ExplicitStrategy::AcrossRhs,
        &solver,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
    );
    let planned_reference = run_explicit(
        ExplicitStrategy::Planned,
        &solver,
        &right_hand_sides,
        options,
        &mut serial_workspace,
        &mut planned_workspace,
    );
    let auto_reference = solver
        .solve_batch_with_workspace(&right_hand_sides, options, &mut auto_workspace)
        .expect("automatic solves should converge");
    let across_difference = validate_results(&serial_reference, &across_reference);
    let planned_difference = validate_results(&serial_reference, &planned_reference);
    let auto_difference = validate_results(&serial_reference, auto_reference.results());
    assert!(across_difference <= 5.0e-9);
    assert!(planned_difference <= 5.0e-9);
    assert!(auto_difference <= 5.0e-9);

    let expected_execution = if rhs_count == 1 {
        if solver.plan().operator_count() > 0 && bench_graph.edges >= 200_000 {
            ParallelPcgExecution::Planned
        } else {
            ParallelPcgExecution::Serial
        }
    } else {
        ParallelPcgExecution::AcrossRightHandSides
    };
    assert_eq!(auto_reference.report().execution(), expected_execution);

    let orders = [
        [
            ExplicitStrategy::Serial,
            ExplicitStrategy::AcrossRhs,
            ExplicitStrategy::Planned,
        ],
        [
            ExplicitStrategy::Planned,
            ExplicitStrategy::AcrossRhs,
            ExplicitStrategy::Serial,
        ],
    ];
    let mut serial_times = Vec::with_capacity(repetitions);
    let mut across_times = Vec::with_capacity(repetitions);
    let mut planned_times = Vec::with_capacity(repetitions);
    let mut auto_times = Vec::with_capacity(repetitions);
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let start = Instant::now();
            let results = solver
                .solve_batch_with_workspace(
                    black_box(&right_hand_sides),
                    options,
                    &mut auto_workspace,
                )
                .expect("automatic solve should converge");
            auto_times.push(start.elapsed().as_nanos());
            black_box(results);
        }
        for strategy in orders[repetition % orders.len()] {
            let start = Instant::now();
            let results = run_explicit(
                strategy,
                &solver,
                black_box(&right_hand_sides),
                options,
                &mut serial_workspace,
                &mut planned_workspace,
            );
            let elapsed = start.elapsed().as_nanos();
            black_box(results);
            match strategy {
                ExplicitStrategy::Serial => serial_times.push(elapsed),
                ExplicitStrategy::AcrossRhs => across_times.push(elapsed),
                ExplicitStrategy::Planned => planned_times.push(elapsed),
            }
        }
        if repetition % 2 == 1 {
            let start = Instant::now();
            let results = solver
                .solve_batch_with_workspace(
                    black_box(&right_hand_sides),
                    options,
                    &mut auto_workspace,
                )
                .expect("automatic solve should converge");
            auto_times.push(start.elapsed().as_nanos());
            black_box(results);
        }
    }

    let serial_ns = median(serial_times);
    let across_ns = median(across_times);
    let planned_ns = median(planned_times);
    let auto_ns = median(auto_times);
    let best_explicit_ns = serial_ns.min(across_ns).min(planned_ns);
    let report = solver
        .select_batch_execution(rhs_count)
        .expect("routing report should be available");
    let selected_explicit_ns = match report.execution() {
        ParallelPcgExecution::Serial => serial_ns,
        ParallelPcgExecution::Planned => planned_ns,
        ParallelPcgExecution::AcrossRightHandSides => across_ns,
    };
    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"rhs_count\":{rhs_count},\"threads\":{},\"levels\":{},\"operators\":{},\"execution\":\"{}\",\"concurrency\":{},\"serial_ns\":{serial_ns},\"across_rhs_ns\":{across_ns},\"planned_ns\":{planned_ns},\"auto_ns\":{auto_ns},\"best_explicit_ns\":{best_explicit_ns},\"selected_explicit_ns\":{selected_explicit_ns},\"auto_over_selected\":{:.17e},\"auto_over_best\":{:.17e},\"serial_over_best\":{:.17e},\"across_over_best\":{:.17e},\"planned_over_best\":{:.17e},\"build_ns\":{build_ns},\"plan_bytes\":{},\"workspace_bytes_each\":{},\"workspace_pool_bytes\":{},\"retained_workspace_bytes\":{},\"max_across_difference\":{across_difference:.17e},\"max_planned_difference\":{planned_difference:.17e},\"max_auto_difference\":{auto_difference:.17e}}}",
        bench_graph.vertices,
        bench_graph.edges,
        solver.executor().thread_count(),
        solver.preconditioner().hierarchy().levels().len(),
        solver.plan().operator_count(),
        execution_name(report.execution()),
        report.concurrency(),
        auto_ns as f64 / selected_explicit_ns as f64,
        auto_ns as f64 / best_explicit_ns as f64,
        serial_ns as f64 / best_explicit_ns as f64,
        across_ns as f64 / best_explicit_ns as f64,
        planned_ns as f64 / best_explicit_ns as f64,
        report.plan_bytes(),
        report.workspace_bytes_each(),
        report.workspace_pool_bytes(),
        auto_workspace.byte_len(),
    );
}
