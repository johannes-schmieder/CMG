use std::hint::black_box;
use std::time::Instant;

use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, PcgWorkspace, solve_pcg_with_workspace,
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

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(50_000);
    let repetitions = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(3)
        .max(1);

    let bench_graph = build_case(&case, scale);
    let rhs = compatible_rhs(&bench_graph.graph);
    let preconditioner = CmgPreconditioner::build(&bench_graph.graph, CmgOptions::default())
        .expect("CMG preconditioner should build");
    let mut workspace = PcgWorkspace::new(&preconditioner);
    let options = PcgOptions::default();

    black_box(
        solve_pcg_with_workspace(
            &bench_graph.graph,
            &preconditioner,
            black_box(&rhs),
            options,
            &mut workspace,
        )
        .expect("warm-up solve should converge"),
    );

    let mut elapsed_ns = Vec::with_capacity(repetitions);
    let mut iterations = 0;
    let mut backward_error = 0.0;
    let mut residual_norm = 0.0;
    for _ in 0..repetitions {
        let start = Instant::now();
        let result = solve_pcg_with_workspace(
            &bench_graph.graph,
            &preconditioner,
            black_box(&rhs),
            options,
            &mut workspace,
        )
        .expect("benchmark solve should converge");
        elapsed_ns.push(start.elapsed().as_nanos());
        iterations = result.iterations();
        backward_error = result.backward_error();
        residual_norm = result.residual_norm();
        black_box(&result);
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"repetitions\":{repetitions},\"median_ns\":{},\"iterations\":{iterations},\"backward_error\":{backward_error:.17e},\"residual_norm\":{residual_norm:.17e},\"workspace_bytes\":{}}}",
        bench_graph.vertices,
        bench_graph.edges,
        preconditioner.hierarchy().levels().len(),
        median(elapsed_ns),
        workspace.byte_len(),
    );
}
