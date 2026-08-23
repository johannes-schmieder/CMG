use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan, ParallelExecutor, ParallelOptions,
    PcgOptions, PcgWorkspace, solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

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
        .map(|(left, right)| {
            let scale = 1.0_f64.max(left.abs()).max(right.abs());
            (left - right).abs() / scale
        })
        .fold(0.0, f64::max)
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
    let preconditioner = CmgPreconditioner::build(
        &bench_graph.graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
    )
    .expect("CMG preconditioner should build");
    let executor = ParallelExecutor::new(ParallelOptions {
        threads,
        min_parallel_len: 16_384,
        ..ParallelOptions::default()
    })
    .expect("parallel executor should build");
    let plan_start = Instant::now();
    let plan =
        ParallelCmgPlan::build(&preconditioner, &executor).expect("parallel CMG plan should build");
    let plan_build_ns = plan_start.elapsed().as_nanos();
    let options = PcgOptions::default();
    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let mut parallel_workspace = PcgWorkspace::new(&preconditioner);

    let serial_warm = solve_pcg_with_workspace(
        &bench_graph.graph,
        &preconditioner,
        &rhs,
        options,
        &mut serial_workspace,
    )
    .expect("serial warm-up should converge");
    let parallel_warm = solve_pcg_with_plan_and_workspace(
        &bench_graph.graph,
        &preconditioner,
        &plan,
        &rhs,
        options,
        &mut parallel_workspace,
        &executor,
    )
    .expect("planned warm-up should converge");
    assert_eq!(serial_warm.iterations(), parallel_warm.iterations());
    let max_difference = max_scaled_difference(serial_warm.solution(), parallel_warm.solution());
    assert!(
        max_difference <= 5.0e-9,
        "parallel PCG changed solution by {max_difference}"
    );

    let mut serial_times = Vec::with_capacity(repetitions);
    let mut parallel_times = Vec::with_capacity(repetitions);
    let mut serial_result = serial_warm;
    let mut parallel_result = parallel_warm;
    for repetition in 0..repetitions {
        if repetition % 2 == 0 {
            let start = Instant::now();
            serial_result = solve_pcg_with_workspace(
                &bench_graph.graph,
                &preconditioner,
                black_box(&rhs),
                options,
                &mut serial_workspace,
            )
            .expect("serial solve should converge");
            serial_times.push(start.elapsed().as_nanos());
            black_box(&serial_result);

            let start = Instant::now();
            parallel_result = solve_pcg_with_plan_and_workspace(
                &bench_graph.graph,
                &preconditioner,
                &plan,
                black_box(&rhs),
                options,
                &mut parallel_workspace,
                &executor,
            )
            .expect("parallel solve should converge");
            parallel_times.push(start.elapsed().as_nanos());
            black_box(&parallel_result);
        } else {
            let start = Instant::now();
            parallel_result = solve_pcg_with_plan_and_workspace(
                &bench_graph.graph,
                &preconditioner,
                &plan,
                black_box(&rhs),
                options,
                &mut parallel_workspace,
                &executor,
            )
            .expect("parallel solve should converge");
            parallel_times.push(start.elapsed().as_nanos());
            black_box(&parallel_result);

            let start = Instant::now();
            serial_result = solve_pcg_with_workspace(
                &bench_graph.graph,
                &preconditioner,
                black_box(&rhs),
                options,
                &mut serial_workspace,
            )
            .expect("serial solve should converge");
            serial_times.push(start.elapsed().as_nanos());
            black_box(&serial_result);
        }
    }

    let serial_ns = median(serial_times);
    let parallel_ns = median(parallel_times);
    let max_difference =
        max_scaled_difference(serial_result.solution(), parallel_result.solution());
    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"threads\":{},\"operators\":{},\"serial_median_ns\":{serial_ns},\"parallel_median_ns\":{parallel_ns},\"speedup\":{:.17e},\"serial_iterations\":{},\"parallel_iterations\":{},\"serial_backward_error\":{:.17e},\"parallel_backward_error\":{:.17e},\"serial_residual_norm\":{:.17e},\"parallel_residual_norm\":{:.17e},\"max_scaled_difference\":{max_difference:.17e},\"plan_build_ns\":{plan_build_ns},\"plan_bytes\":{},\"workspace_bytes\":{}}}",
        bench_graph.vertices,
        bench_graph.edges,
        preconditioner.hierarchy().levels().len(),
        executor.thread_count(),
        plan.operator_count(),
        serial_ns as f64 / parallel_ns as f64,
        serial_result.iterations(),
        parallel_result.iterations(),
        serial_result.backward_error(),
        parallel_result.backward_error(),
        serial_result.residual_norm(),
        parallel_result.residual_norm(),
        plan.byte_len(),
        serial_workspace.byte_len(),
    );
}
