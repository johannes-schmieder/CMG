use std::hint::black_box;
use std::time::Instant;

use cmg::{Aggregation, CmgHierarchy, CmgOptions, Laplacian};

#[derive(Clone, Copy, Debug, PartialEq)]
struct ProbeEdge {
    u: u32,
    v: u32,
    weight: f64,
}

#[derive(Clone, Copy)]
enum SortMode {
    Comparison,
    RoutedRadix,
}

#[derive(Default)]
struct Timings {
    mapping: Vec<u128>,
    sorting: Vec<u128>,
    merging: Vec<u128>,
    diagonal: Vec<u128>,
    finalize: Vec<u128>,
    production: Vec<u128>,
}

fn median(values: &mut [u128]) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn endpoint_key(edge: &ProbeEdge) -> u64 {
    (u64::from(edge.u) << 32) | u64::from(edge.v)
}

fn compare_edges(left: &ProbeEdge, right: &ProbeEdge) -> core::cmp::Ordering {
    endpoint_key(left)
        .cmp(&endpoint_key(right))
        .then_with(|| left.weight.total_cmp(&right.weight))
}

const RADIX_BUCKETS: usize = 256;
const RADIX_SORT_MIN_EDGES: usize = 1 << 18;
const RADIX_SORT_MIN_EDGES_PER_VERTEX: usize = 4;
const RADIX_SORT_SMALL_BUCKET: usize = 64;

fn should_use_radix(vertex_count: usize, edge_count: usize) -> bool {
    edge_count >= RADIX_SORT_MIN_EDGES
        && edge_count >= vertex_count.saturating_mul(RADIX_SORT_MIN_EDGES_PER_VERTEX)
}

fn radix_sort_endpoint_keys(raw: &mut [ProbeEdge], shift: u32) {
    if raw.len() <= 1 {
        return;
    }
    if raw.len() <= RADIX_SORT_SMALL_BUCKET {
        raw.sort_unstable_by_key(endpoint_key);
        return;
    }
    let mut boundaries = [0usize; RADIX_BUCKETS + 1];
    for edge in raw.iter() {
        let bucket = ((endpoint_key(edge) >> shift) & 0xff) as usize;
        boundaries[bucket + 1] += 1;
    }
    for bucket in 0..RADIX_BUCKETS {
        boundaries[bucket + 1] += boundaries[bucket];
    }
    let mut next = [0usize; RADIX_BUCKETS];
    next.copy_from_slice(&boundaries[..RADIX_BUCKETS]);
    for bucket in 0..RADIX_BUCKETS {
        let bucket_end = boundaries[bucket + 1];
        while next[bucket] < bucket_end {
            let index = next[bucket];
            let target = ((endpoint_key(&raw[index]) >> shift) & 0xff) as usize;
            if target == bucket {
                next[bucket] += 1;
            } else {
                let target_index = next[target];
                raw.swap(index, target_index);
                next[target] += 1;
            }
        }
    }
    if shift >= 8 {
        let next_shift = shift - 8;
        for bucket in 0..RADIX_BUCKETS {
            let start = boundaries[bucket];
            let end = boundaries[bucket + 1];
            if end - start > 1 {
                radix_sort_endpoint_keys(&mut raw[start..end], next_shift);
            }
        }
    }
}

fn sort_weights_within_groups(raw: &mut [ProbeEdge]) {
    let mut start = 0;
    while start < raw.len() {
        let key = endpoint_key(&raw[start]);
        let mut end = start + 1;
        while end < raw.len() && endpoint_key(&raw[end]) == key {
            end += 1;
        }
        raw[start..end].sort_unstable_by(|left, right| left.weight.total_cmp(&right.weight));
        start = end;
    }
}

fn sort_edges(raw: &mut [ProbeEdge], vertex_count: usize, mode: SortMode) -> bool {
    if matches!(mode, SortMode::RoutedRadix) && should_use_radix(vertex_count, raw.len()) {
        radix_sort_endpoint_keys(raw, 56);
        sort_weights_within_groups(raw);
        true
    } else {
        raw.sort_unstable_by(compare_edges);
        false
    }
}

fn compensated_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let next = sum + value;
        if sum.abs() >= value.abs() {
            correction += (sum - next) + value;
        } else {
            correction += (value - next) + sum;
        }
        sum = next;
    }
    sum + correction
}

fn map_edges(graph: &Laplacian, aggregation: &Aggregation) -> Vec<ProbeEdge> {
    let labels = aggregation.labels();
    let mut mapped = Vec::with_capacity(graph.edge_count());
    for edge in graph.edges() {
        let left = labels[edge.u()];
        let right = labels[edge.v()];
        if left == right {
            continue;
        }
        let (u, v) = if left < right {
            (left, right)
        } else {
            (right, left)
        };
        mapped.push(ProbeEdge {
            u: u32::try_from(u).expect("coarse endpoint fits u32"),
            v: u32::try_from(v).expect("coarse endpoint fits u32"),
            weight: edge.weight(),
        });
    }
    mapped
}

fn merge_sorted_edges(raw: &mut Vec<ProbeEdge>) {
    let mut read = 0usize;
    let mut write = 0usize;
    while read < raw.len() {
        let u = raw[read].u;
        let v = raw[read].v;
        let mut end = read + 1;
        while end < raw.len() && raw[end].u == u && raw[end].v == v {
            end += 1;
        }
        let weight = compensated_sum(raw[read..end].iter().map(|edge| edge.weight));
        raw[write] = ProbeEdge { u, v, weight };
        write += 1;
        read = end;
    }
    raw.truncate(write);
    raw.shrink_to_fit();
}

fn build_diagonal(vertex_count: usize, edges: &[ProbeEdge]) -> Vec<f64> {
    let mut diagonal = vec![0.0; vertex_count];
    for edge in edges {
        diagonal[edge.u as usize] += edge.weight;
        diagonal[edge.v as usize] += edge.weight;
    }
    diagonal
}

fn verify(edges: &[ProbeEdge], diagonal: &[f64], expected: &Laplacian) {
    assert_eq!(edges.len(), expected.edge_count());
    for (candidate, reference) in edges.iter().zip(expected.edges()) {
        assert_eq!(candidate.u as usize, reference.u());
        assert_eq!(candidate.v as usize, reference.v());
        assert_eq!(candidate.weight.to_bits(), reference.weight().to_bits());
    }
    assert_eq!(diagonal.len(), expected.diagonal().len());
    for (candidate, reference) in diagonal.iter().zip(expected.diagonal()) {
        assert_eq!(candidate.to_bits(), reference.to_bits());
    }
}

fn profile_level(
    graph: &Laplacian,
    aggregation: &Aggregation,
    expected: &Laplacian,
    repetitions: usize,
    mode: SortMode,
) -> (Timings, usize, usize, bool) {
    let mut timings = Timings::default();
    let mut mapped_count = 0usize;
    let mut merged_count = 0usize;
    let mut used_radix = false;

    for _ in 0..repetitions {
        let map_start = Instant::now();
        let mut mapped = map_edges(graph, aggregation);
        timings.mapping.push(map_start.elapsed().as_nanos());
        mapped_count = mapped.len();

        let sort_start = Instant::now();
        used_radix |= sort_edges(&mut mapped, aggregation.coarse_dimension(), mode);
        timings.sorting.push(sort_start.elapsed().as_nanos());

        let merge_start = Instant::now();
        merge_sorted_edges(&mut mapped);
        timings.merging.push(merge_start.elapsed().as_nanos());
        merged_count = mapped.len();

        let diagonal_start = Instant::now();
        let diagonal = build_diagonal(aggregation.coarse_dimension(), &mapped);
        timings.diagonal.push(diagonal_start.elapsed().as_nanos());

        let finalize_start = Instant::now();
        let diagonal_nonzeros = diagonal.iter().filter(|value| **value != 0.0).count();
        let matrix_nonzeros = diagonal_nonzeros + 2 * mapped.len();
        let operator_norm_bound = diagonal.iter().copied().fold(0.0_f64, f64::max) * 2.0;
        timings.finalize.push(finalize_start.elapsed().as_nanos());
        assert_eq!(matrix_nonzeros, expected.matrix_nnz());
        assert_eq!(
            operator_norm_bound.to_bits(),
            expected.operator_norm_bound().to_bits(),
        );

        verify(&mapped, &diagonal, expected);
        black_box((&mapped, &diagonal));

        let production_start = Instant::now();
        let production = aggregation
            .contract(black_box(graph))
            .expect("production contraction should succeed");
        timings
            .production
            .push(production_start.elapsed().as_nanos());
        assert_eq!(&production, expected);
        black_box(production);
    }

    (timings, mapped_count, merged_count, used_radix)
}

struct BenchGraph {
    graph: Laplacian,
    vertices: usize,
    edges: usize,
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
        _ => panic!("unknown case"),
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let case = args.next().unwrap_or_else(|| "worker-firm".to_owned());
    let scale = args
        .next()
        .map(|value| value.parse::<usize>().expect("integer scale"))
        .unwrap_or(100_000);
    let repetitions = args
        .next()
        .map(|value| value.parse::<usize>().expect("integer repetitions"))
        .unwrap_or(3)
        .max(1);
    let mode = match args.next().as_deref() {
        Some("radix") => SortMode::RoutedRadix,
        Some("comparison") | None => SortMode::Comparison,
        Some(_) => panic!("sort mode must be comparison or radix"),
    };

    let bench = build_case(&case, scale);
    let hierarchy =
        CmgHierarchy::build(&bench.graph, CmgOptions::default()).expect("hierarchy should build");

    let mut total_mapping = 0u128;
    let mut total_sorting = 0u128;
    let mut total_merging = 0u128;
    let mut total_diagonal = 0u128;
    let mut total_finalize = 0u128;
    let mut total_production = 0u128;
    let mut profiled_levels = 0usize;

    for (index, pair) in hierarchy.levels().windows(2).enumerate() {
        let fine = &pair[0];
        let coarse = &pair[1];
        let Some(aggregation) = fine.aggregation() else {
            continue;
        };
        let (mut timings, mapped_count, merged_count, used_radix) =
            profile_level(fine.graph(), aggregation, coarse.graph(), repetitions, mode);
        let mapping = median(&mut timings.mapping);
        let sorting = median(&mut timings.sorting);
        let merging = median(&mut timings.merging);
        let diagonal = median(&mut timings.diagonal);
        let finalize = median(&mut timings.finalize);
        let production = median(&mut timings.production);
        total_mapping += mapping;
        total_sorting += sorting;
        total_merging += merging;
        total_diagonal += diagonal;
        total_finalize += finalize;
        total_production += production;
        profiled_levels += 1;
        println!(
            "{{\"record\":\"level\",\"case\":\"{case}\",\"level\":{index},\"fine_vertices\":{},\"fine_edges\":{},\"coarse_vertices\":{},\"mapped_edges\":{mapped_count},\"merged_edges\":{merged_count},\"used_radix\":{used_radix},\"mapping_ns\":{mapping},\"sorting_ns\":{sorting},\"merging_ns\":{merging},\"diagonal_ns\":{diagonal},\"finalize_ns\":{finalize},\"production_ns\":{production}}}",
            fine.graph().vertex_count(),
            fine.graph().edge_count(),
            coarse.graph().vertex_count(),
        );
    }

    let manual_total =
        total_mapping + total_sorting + total_merging + total_diagonal + total_finalize;
    let ratio = if total_production == 0 {
        0.0
    } else {
        manual_total as f64 / total_production as f64
    };
    println!(
        "{{\"record\":\"case\",\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"levels\":{},\"profiled_levels\":{profiled_levels},\"mapping_ns\":{total_mapping},\"sorting_ns\":{total_sorting},\"merging_ns\":{total_merging},\"diagonal_ns\":{total_diagonal},\"finalize_ns\":{total_finalize},\"manual_total_ns\":{manual_total},\"production_total_ns\":{total_production},\"manual_over_production\":{ratio}}}",
        bench.vertices,
        bench.edges,
        hierarchy.levels().len(),
    );
}
