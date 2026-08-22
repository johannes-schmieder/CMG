use std::hint::black_box;
use std::time::Instant;

use cmg::Laplacian;

fn median(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn unique_path(edges: usize) -> (usize, Vec<(usize, usize, f64)>) {
    let vertices = edges + 1;
    let input = (0..edges)
        .map(|vertex| (vertex, vertex + 1, 1.0))
        .collect();
    (vertices, input)
}

fn duplicate_pairs(pairs: usize, duplicates: usize) -> (usize, Vec<(usize, usize, f64)>) {
    let vertices = 2 * pairs;
    let mut input = Vec::with_capacity(pairs * duplicates);
    for left in 0..pairs {
        let right = pairs + left;
        for duplicate in 0..duplicates {
            let weight = 0.25 + duplicate as f64 / duplicates as f64;
            input.push((left, right, weight));
        }
    }
    (vertices, input)
}

fn coarse_collisions(groups: usize, fanout: usize) -> (usize, Vec<(usize, usize, f64)>) {
    let vertices = 2 * groups;
    let mut input = Vec::with_capacity(groups * fanout);
    for group in 0..groups {
        for edge in 0..fanout {
            let right_group = (group + 1 + edge % 3) % groups;
            let weight = 0.125 + (edge % 17) as f64 / 32.0;
            input.push((group, groups + right_group, weight));
        }
    }
    (vertices, input)
}

fn make_case(case: &str, scale: usize) -> (usize, Vec<(usize, usize, f64)>) {
    match case {
        "unique" => unique_path(scale),
        "duplicates-4" => duplicate_pairs(scale, 4),
        "duplicates-16" => duplicate_pairs(scale, 16),
        "coarse-collisions" => coarse_collisions(scale, 16),
        _ => panic!(
            "unknown case {case}; expected unique, duplicates-4, duplicates-16, or coarse-collisions"
        ),
    }
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let case = arguments.next().unwrap_or_else(|| "duplicates-4".to_owned());
    let scale = arguments
        .next()
        .map(|argument| argument.parse::<usize>().expect("scale must be an integer"))
        .unwrap_or(250_000);
    let repetitions = arguments
        .next()
        .map(|argument| {
            argument
                .parse::<usize>()
                .expect("repetitions must be an integer")
        })
        .unwrap_or(5)
        .max(1);

    let (vertices, raw_edges) = make_case(&case, scale);
    let raw_edge_count = raw_edges.len();

    black_box(
        Laplacian::from_edges(vertices, raw_edges.clone())
            .expect("benchmark graph should be valid"),
    );

    let mut elapsed_ns = Vec::with_capacity(repetitions);
    let mut retained_edges = 0usize;
    for _ in 0..repetitions {
        let input = raw_edges.clone();
        let start = Instant::now();
        let graph = Laplacian::from_edges(vertices, input)
            .expect("benchmark graph should be valid");
        elapsed_ns.push(start.elapsed().as_nanos());
        retained_edges = graph.edge_count();
        black_box(graph);
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{vertices},\"raw_edges\":{raw_edge_count},\"retained_edges\":{retained_edges},\"repetitions\":{repetitions},\"median_ns\":{}}}",
        median(elapsed_ns),
    );
}
