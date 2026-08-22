use std::alloc::{GlobalAlloc, Layout, System};
use std::hint::black_box;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use cmg::{CmgOptions, CmgPreconditioner, Laplacian};

struct TrackingAllocator;

static CURRENT_BYTES: AtomicUsize = AtomicUsize::new(0);
static PEAK_BYTES: AtomicUsize = AtomicUsize::new(0);

#[global_allocator]
static GLOBAL_ALLOCATOR: TrackingAllocator = TrackingAllocator;

unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            record_allocation(layout.size());
        }
        pointer
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() {
            record_allocation(layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        record_deallocation(layout.size());
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let new_pointer = unsafe { System.realloc(pointer, layout, new_size) };
        if !new_pointer.is_null() {
            if new_size >= layout.size() {
                record_allocation(new_size - layout.size());
            } else {
                record_deallocation(layout.size() - new_size);
            }
        }
        new_pointer
    }
}

fn record_allocation(bytes: usize) {
    if bytes == 0 {
        return;
    }
    let current = CURRENT_BYTES.fetch_add(bytes, Ordering::SeqCst) + bytes;
    PEAK_BYTES.fetch_max(current, Ordering::SeqCst);
}

fn record_deallocation(bytes: usize) {
    if bytes != 0 {
        CURRENT_BYTES.fetch_sub(bytes, Ordering::SeqCst);
    }
}

fn current_bytes() -> usize {
    CURRENT_BYTES.load(Ordering::SeqCst)
}

fn reset_peak_to_current() -> usize {
    let current = current_bytes();
    PEAK_BYTES.store(current, Ordering::SeqCst);
    current
}

fn peak_bytes() -> usize {
    PEAK_BYTES.load(Ordering::SeqCst)
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

fn median_u128(mut values: Vec<u128>) -> u128 {
    values.sort_unstable();
    values[values.len() / 2]
}

fn median_usize(mut values: Vec<usize>) -> usize {
    values.sort_unstable();
    values[values.len() / 2]
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

    let bench_graph = build_case(&case, scale);
    drop(
        CmgPreconditioner::build(black_box(&bench_graph.graph), CmgOptions::default())
            .expect("CMG hierarchy warmup should succeed"),
    );

    let mut elapsed_ns = Vec::with_capacity(repetitions);
    let mut additional_peak_bytes = Vec::with_capacity(repetitions);
    let mut retained_bytes = Vec::with_capacity(repetitions);
    let mut post_drop_deltas = Vec::with_capacity(repetitions);
    let mut hierarchy_levels = 0usize;
    let mut hierarchy_matrix_nonzeros = 0usize;

    for _ in 0..repetitions {
        let baseline_current = reset_peak_to_current();
        let start = Instant::now();
        let preconditioner =
            CmgPreconditioner::build(black_box(&bench_graph.graph), CmgOptions::default())
                .expect("CMG hierarchy build should succeed");
        elapsed_ns.push(start.elapsed().as_nanos());
        additional_peak_bytes.push(peak_bytes().saturating_sub(baseline_current));
        retained_bytes.push(current_bytes().saturating_sub(baseline_current));
        hierarchy_levels = preconditioner.hierarchy().levels().len();
        hierarchy_matrix_nonzeros = preconditioner
            .hierarchy()
            .levels()
            .iter()
            .map(|level| level.graph().matrix_nnz())
            .sum();
        black_box(&preconditioner);
        drop(preconditioner);
        let after_drop = current_bytes();
        post_drop_deltas.push(after_drop.abs_diff(baseline_current));
    }

    println!(
        "{{\"case\":\"{case}\",\"scale\":{scale},\"vertices\":{},\"edges\":{},\"repetitions\":{repetitions},\"levels\":{hierarchy_levels},\"hierarchy_matrix_nonzeros\":{hierarchy_matrix_nonzeros},\"median_ns\":{},\"median_additional_peak_bytes\":{},\"median_retained_bytes\":{},\"max_post_drop_delta_bytes\":{}}}",
        bench_graph.vertices,
        bench_graph.edges,
        median_u128(elapsed_ns),
        median_usize(additional_peak_bytes),
        median_usize(retained_bytes),
        post_drop_deltas.into_iter().max().unwrap_or(0),
    );
}
