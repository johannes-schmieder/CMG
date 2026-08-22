use std::hint::black_box;
use std::time::Instant;

use cmg::{CmgOptions, CmgPreconditioner, Laplacian};

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
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    let edge_count = edges.len();
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid path graph"),
        vertices,
        edges: edge_count,
    }
}

fn grid_graph(side: usize) -> BenchGraph {
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
    BenchGraph {
        graph: Laplacian::from_edges(vertices, edges).expect("valid grid graph"),
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
        graph: Laplacian::from_edges(vertices, edges)
            .expect("valid worker-firm graph"),
        vertices,
        edges: edge_count,
    }
}

fn build_case(case: &str, scale: usize) -> BenchGraph {
    match case {
        "path" => path_graph(scale),
        "grid" => grid_graph(scale),
        "worker-firm" => worker_firm_graph(scale, 3),
        "dense-worker-firm" => worker_firm_graph(scale, 16),
        _ => panic!(
            "unknown case {case}; expected path, grid, worker-firm, or dense-worker-firm"
        ),
    }
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

    black_box(
        CmgPreconditioner::build(
            black_box(&bench_graph.graph),
            CmgOptions::default(),
        )
        .expect("CMG hierarchy build should succeed"),
    );

    let mut elapsed_ns = Vec::with_capacity(repetitions);
    for _ in 0..repetitions {
        let start = Instant::now();
        let preconditioner = CmgPreconditioner::build(
            black_box(&bench_graph.graph),
            CmgOptions::default(),
        )
        .expect("CMG hierarchy build should succeed");
        elapsed_ns.push(start.elapsed().as_nanos());
        black_box(preconditioner);
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"repetitions\":{repetitions},\"median_ns\":{}}}",
        bench_graph.vertices,
        bench_graph.edges,
        median(elapsed_ns),
    );
}
