use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, Laplacian, ParallelOptions, ParallelPcgSolver, PcgOptions, PcgResult, PcgWorkspace,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    input_edges: usize,
    canonical_edges: usize,
}

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> BenchGraph {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 0.5 + (vertex % 31) as f64 / 17.0))
        .collect();
    let input_edges = edges.len();
    let graph = Laplacian::from_edges(vertices, edges).expect("valid path graph");
    let canonical_edges = graph.edges().len();
    BenchGraph {
        graph,
        vertices,
        input_edges,
        canonical_edges,
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
    let input_edges = edges.len();
    let graph = Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph");
    let canonical_edges = graph.edges().len();
    BenchGraph {
        graph,
        vertices,
        input_edges,
        canonical_edges,
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

fn compatible_rhs(graph: &Laplacian) -> Vec<f64> {
    let mut target: Vec<f64> = (0..graph.vertex_count())
        .map(|vertex| {
            let centered = (vertex % 257) as f64 - 128.0;
            centered / 37.0 + ((vertex * 17) % 19) as f64 / 101.0
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
        .map(|(&a, &b)| (a - b).abs() / (1.0 + a.abs().max(b.abs())))
        .fold(0.0, f64::max)
}

fn max_identifiable_scaled_difference(graph: &Laplacian, left: &[f64], right: &[f64]) -> f64 {
    graph
        .edges()
        .iter()
        .map(|edge| {
            let left_difference = left[edge.u()] - left[edge.v()];
            let right_difference = right[edge.u()] - right[edge.v()];
            (left_difference - right_difference).abs()
                / (1.0 + left_difference.abs().max(right_difference.abs()))
        })
        .fold(0.0, f64::max)
}

fn time_serial(
    solver: &ParallelPcgSolver,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> (u128, PcgResult) {
    let start = Instant::now();
    let result = solve_pcg_with_workspace(
        solver.graph(),
        solver.preconditioner(),
        black_box(rhs),
        options,
        workspace,
    )
    .expect("serial benchmark solve should converge");
    (start.elapsed().as_nanos(), result)
}

fn time_planned(
    solver: &ParallelPcgSolver,
    rhs: &[f64],
    options: PcgOptions,
    workspace: &mut PcgWorkspace,
) -> (u128, PcgResult) {
    let start = Instant::now();
    let result = solve_pcg_with_plan_and_workspace(
        solver.graph(),
        solver.preconditioner(),
        solver.plan(),
        black_box(rhs),
        options,
        workspace,
        solver.executor(),
    )
    .expect("planned benchmark solve should converge");
    (start.elapsed().as_nanos(), result)
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(100_000);
    let repetitions = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(3)
        .max(1);
    let threads = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("threads must be an integer")
        })
        .unwrap_or(4)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let rhs = compatible_rhs(&bench_graph.graph);
    let solver = ParallelPcgSolver::build(
        &bench_graph.graph,
        CmgOptions::default(),
        ParallelOptions {
            threads,
            ..ParallelOptions::default()
        },
    )
    .expect("prepared parallel solver should build");
    let options = PcgOptions::default();
    let mut serial_workspace = PcgWorkspace::new(solver.preconditioner());
    let mut planned_workspace = PcgWorkspace::new(solver.preconditioner());

    let (_, serial_warm) = time_serial(&solver, &rhs, options, &mut serial_workspace);
    let (_, planned_warm) = time_planned(&solver, &rhs, options, &mut planned_workspace);
    black_box((&serial_warm, &planned_warm));

    let mut serial_ns = Vec::with_capacity(repetitions);
    let mut planned_ns = Vec::with_capacity(repetitions);
    let mut serial_result = serial_warm;
    let mut planned_result = planned_warm;

    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let (elapsed, result) = time_serial(&solver, &rhs, options, &mut serial_workspace);
            serial_ns.push(elapsed);
            serial_result = result;
            let (elapsed, result) = time_planned(&solver, &rhs, options, &mut planned_workspace);
            planned_ns.push(elapsed);
            planned_result = result;
        } else {
            let (elapsed, result) = time_planned(&solver, &rhs, options, &mut planned_workspace);
            planned_ns.push(elapsed);
            planned_result = result;
            let (elapsed, result) = time_serial(&solver, &rhs, options, &mut serial_workspace);
            serial_ns.push(elapsed);
            serial_result = result;
        }
        black_box((&serial_result, &planned_result));
    }

    let serial_median_ns = median(serial_ns);
    let planned_median_ns = median(planned_ns);
    let speedup = serial_median_ns as f64 / planned_median_ns as f64;
    let difference = max_scaled_difference(serial_result.solution(), planned_result.solution());
    let identifiable_difference = max_identifiable_scaled_difference(
        solver.graph(),
        serial_result.solution(),
        planned_result.solution(),
    );
    let execution = format!(
        "{:?}",
        solver
            .select_batch_execution(1)
            .expect("routing report should succeed")
            .execution()
    );

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"input_edges\":{},\"edges\":{},\"levels\":{},\"repetitions\":{repetitions},\"threads\":{threads},\"operators\":{},\"plan_bytes\":{},\"workspace_bytes\":{},\"auto_execution\":\"{execution}\",\"serial_median_ns\":{serial_median_ns},\"planned_median_ns\":{planned_median_ns},\"speedup\":{speedup:.17e},\"serial_iterations\":{},\"planned_iterations\":{},\"serial_backward_error\":{:.17e},\"planned_backward_error\":{:.17e},\"serial_residual_norm\":{:.17e},\"planned_residual_norm\":{:.17e},\"max_scaled_difference\":{difference:.17e},\"max_identifiable_scaled_difference\":{identifiable_difference:.17e}}}",
        bench_graph.vertices,
        bench_graph.input_edges,
        bench_graph.canonical_edges,
        solver.preconditioner().hierarchy().levels().len(),
        solver.plan().operator_count(),
        solver.plan().byte_len(),
        serial_workspace.byte_len(),
        serial_result.iterations(),
        planned_result.iterations(),
        serial_result.backward_error(),
        planned_result.backward_error(),
        serial_result.residual_norm(),
        planned_result.residual_norm(),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiable_difference_ignores_laplacian_null_space_shift() {
        let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 2.0)]).unwrap();
        let left = [1.0, -2.0, 4.0];
        let right = [8.0, 5.0, 11.0];
        assert!(max_scaled_difference(&left, &right) > 0.0);
        assert_eq!(
            max_identifiable_scaled_difference(&graph, &left, &right),
            0.0
        );
    }
}
