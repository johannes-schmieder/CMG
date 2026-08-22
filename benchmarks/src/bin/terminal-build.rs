use std::hint::black_box;
use std::time::Instant;

use cmg::{CmgOptions, CmgPreconditioner, Laplacian};

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn path_graph(vertices: usize) -> (Laplacian, usize) {
    let edges: Vec<_> = (0..vertices.saturating_sub(1))
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let edge_count = edges.len();
    (
        Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        edge_count,
    )
}

fn grid_graph(side: usize) -> (Laplacian, usize) {
    let vertices = side * side;
    let mut edges = Vec::with_capacity(2 * vertices);
    for row in 0..side {
        for column in 0..side {
            let vertex = row * side + column;
            if column + 1 < side {
                edges.push((vertex, vertex + 1, 1.0));
            }
            if row + 1 < side {
                edges.push((vertex, vertex + side, 1.0));
            }
        }
    }
    let edge_count = edges.len();
    (
        Laplacian::from_edges(vertices, edges).expect("valid grid graph"),
        edge_count,
    )
}

fn worker_firm_graph(per_side: usize) -> (Laplacian, usize) {
    let vertices = 2 * per_side;
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(3 * per_side);
    for worker in 0..per_side {
        edges.push((worker, firm_offset + worker, 1.0));
        edges.push((worker, firm_offset + (worker + 1) % per_side, 0.75));
        edges.push((
            worker,
            firm_offset + (17 * worker + 3) % per_side,
            0.25 + (worker % 11) as f64 / 20.0,
        ));
    }
    let edge_count = edges.len();
    (
        Laplacian::from_edges(vertices, edges).expect("valid worker-firm graph"),
        edge_count,
    )
}

fn benchmark_case(name: &str, graph: &Laplacian, edge_count: usize, repetitions: usize) {
    let options = CmgOptions::default();

    for _ in 0..2 {
        black_box(
            CmgPreconditioner::build(black_box(graph), options.clone())
                .expect("terminal preconditioner build should succeed"),
        );
    }

    let mut elapsed_ns = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let preconditioner = CmgPreconditioner::build(black_box(graph), options.clone())
            .expect("terminal preconditioner build should succeed");
        elapsed_ns.push(start.elapsed().as_nanos());
        black_box(preconditioner);
    }

    println!(
        "{{\"case\":\"{name}\",\"vertices\":{},\"edges\":{edge_count},\"repetitions\":{repetitions},\"median_ns\":{}}}",
        graph.n_vertices(),
        median(elapsed_ns),
    );
}

fn main() {
    let repetitions = std::env::args()
        .nth(1)
        .map(|argument| argument.parse::<usize>().expect("repetitions must be an integer"))
        .unwrap_or(9)
        .max(1);

    let (path, path_edges) = path_graph(600);
    benchmark_case("path-600", &path, path_edges, repetitions);

    let (grid, grid_edges) = grid_graph(24);
    benchmark_case("grid-24x24", &grid, grid_edges, repetitions);

    let (worker_firm, worker_firm_edges) = worker_firm_graph(300);
    benchmark_case(
        "worker-firm-300x300",
        &worker_firm,
        worker_firm_edges,
        repetitions,
    );
}
