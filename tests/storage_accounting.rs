//! Independent requested-allocation checks. One serial test owns the tracer;
//! no production allocator cap is installed and failures target fallible paths.
use cmg::*;
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

thread_local! {
    static LIVE: Cell<isize> = const { Cell::new(0) };
    static PEAK: Cell<isize> = const { Cell::new(0) };
    static CALLS: Cell<usize> = const { Cell::new(0) };
    static FAIL: Cell<Option<usize>> = const { Cell::new(None) };
}
struct Observed;
fn allocated(bytes: usize) {
    let _ = LIVE.try_with(|live| {
        live.set(live.get() + bytes as isize);
        let _ = PEAK.try_with(|peak| peak.set(peak.get().max(live.get())));
    });
}
fn fail() -> bool {
    let _ = CALLS.try_with(|calls| calls.set(calls.get() + 1));
    FAIL.try_with(|fail| match fail.get() {
        Some(0) => {
            fail.set(None);
            true
        }
        Some(n) => {
            fail.set(Some(n - 1));
            false
        }
        None => false,
    })
    .unwrap_or(false)
}
unsafe impl GlobalAlloc for Observed {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if fail() {
            return std::ptr::null_mut();
        }
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            allocated(layout.size());
        }
        ptr
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        let _ = LIVE.try_with(|live| live.set(live.get() - layout.size() as isize));
        unsafe {
            System.dealloc(ptr, layout);
        }
    }
    unsafe fn realloc(&self, ptr: *mut u8, old: Layout, size: usize) -> *mut u8 {
        if fail() {
            return std::ptr::null_mut();
        }
        let ptr = unsafe { System.realloc(ptr, old, size) };
        if !ptr.is_null() {
            // A realloc may temporarily retain both buffers; record that upper
            // observation before removing the old requested layout.
            allocated(size);
            let _ = LIVE.try_with(|live| live.set(live.get() - old.size() as isize));
        }
        ptr
    }
}
#[global_allocator]
static ALLOCATOR: Observed = Observed;
fn live() -> isize {
    LIVE.with(Cell::get)
}
fn report(roots: &[&dyn CmgStorage]) -> usize {
    let calls = CALLS.with(Cell::get);
    let bytes = CmgStorageReport::measure(roots)
        .unwrap()
        .total_bytes()
        .unwrap();
    assert_eq!(CALLS.with(Cell::get), calls, "report must not allocate");
    bytes
}

#[test]
fn independent_capacity_admission_overlap_and_failure_checks() {
    let endpoints: Vec<_> = (1..85)
        .filter(|i| i % 11 != 0)
        .map(|i| (i - 1, i))
        .chain([(0, 1), (0, 1)])
        .collect();
    let weights = vec![1.0; endpoints.len()];
    let base = live();
    let bound =
        PreparedLaplacianTopology::preparation_allocation_bound(91, endpoints.len()).unwrap();
    assert!(PreparedLaplacianTopology::prepare_with_budget(91, &endpoints, bound - 1).is_err());
    assert_eq!(live(), base);
    PEAK.with(|p| p.set(base));
    let topology = PreparedLaplacianTopology::prepare_with_budget(91, &endpoints, bound).unwrap();
    assert!(PEAK.with(Cell::get) - base <= bound as isize);
    assert_eq!(report(&[&topology]), (live() - base) as usize);
    let mut assembly = topology.workspace().unwrap();
    let before = live();
    let numeric = topology.numeric_allocation_bytes().unwrap();
    assert!(
        topology
            .assemble_with_workspace_budgeted(&weights, &mut assembly, numeric - 1)
            .is_err()
    );
    assert_eq!(live(), before);
    let graph = topology
        .assemble_with_workspace_budgeted(&weights, &mut assembly, numeric)
        .unwrap();
    assert_eq!((live() - before) as usize, numeric);
    let pre = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: 12,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(
        report(&[&topology, &assembly, &graph, &pre]),
        (live() - base) as usize
    );
    let repeated = report(&[&topology, &assembly, &graph, &pre, &graph, &pre]);
    assert_eq!(repeated, (live() - base) as usize);

    let bank_bytes = PcgBatchWorkspace::bank_allocation_bytes(&pre, 3).unwrap();
    let before_bank = live();
    assert!(PcgBatchWorkspace::new_bank_with_budget(&pre, 3, bank_bytes - 1).is_err());
    assert_eq!(live(), before_bank);
    let bank = PcgBatchWorkspace::new_bank_with_budget(&pre, 3, bank_bytes).unwrap();
    assert_eq!(report(&[&bank]), bank_bytes);
    assert_eq!((live() - before_bank) as usize, bank_bytes);
    let replacement = PcgBatchWorkspace::new_bank_with_budget(&pre, 1, bank_bytes).unwrap();
    let next_graph = topology.assemble(&weights).unwrap();
    assert_eq!(
        report(&[
            &topology,
            &assembly,
            &graph,
            &next_graph,
            &pre,
            &bank,
            &replacement
        ]),
        (live() - base) as usize
    );
    drop((replacement, next_graph, bank));
    assert_eq!(live(), before_bank);

    // Inject each fallible allocation failure in a two-workspace bank, including
    // failures after the first workspace is complete. Every partial owner drops.
    let mut failure_count = 0;
    for index in 0..300 {
        let before = live();
        FAIL.with(|fail| fail.set(Some(index)));
        let result = PcgBatchWorkspace::new_bank_with_budget(&pre, 2, usize::MAX);
        FAIL.with(|fail| fail.set(None));
        let success = result.is_ok();
        drop(result);
        assert_eq!(live(), before, "partial bank leaked at allocation {index}");
        if success {
            break;
        }
        failure_count += 1;
    }
    assert!(failure_count > 30);
    assert!(PcgBatchWorkspace::bank_allocation_bytes(&pre, usize::MAX).is_err());
    assert!(PreparedLaplacianTopology::preparation_allocation_bound(usize::MAX, 1).is_err());
    assert!(PreparedLaplacianTopology::preparation_allocation_bound(1, usize::MAX).is_err());

    // Spare capacities, including outer banks and compact label backing memory.
    let mut spare: Vec<Vec<f64>> = Vec::with_capacity(17);
    spare.push(Vec::with_capacity(100));
    assert_eq!(
        report(&[&spare]),
        17 * std::mem::size_of::<Vec<f64>>() + 800
    );
    let mut labels = Vec::with_capacity(96);
    labels.extend([0usize, 1, 1, 0]);
    let before = live();
    let labels_bytes = 96 * std::mem::size_of::<usize>();
    let aggregation = Aggregation::new(labels, 2).unwrap();
    assert_eq!(
        report(&[&aggregation]),
        (live() - before + labels_bytes as isize) as usize
    );
    let old = report(&[&aggregation]);
    let _ = aggregation.labels();
    let _ = aggregation.sizes();
    assert!(report(&[&aggregation]) >= old);
    drop((spare, aggregation));
    drop((pre, graph, assembly, topology));
    assert_eq!(live(), base);
}

#[test]
fn fill_crossing_and_isolates_are_not_a_nonzero_storage_cap() {
    for fill in [0.01, 1.0, 1.25, 5.0] {
        for max_levels in [1, 2, 16] {
            for isolates in [0, 1000] {
                let graph =
                    Laplacian::from_edges(128 + isolates, (1..128).map(|v| (v - 1, v, 1.0)))
                        .unwrap();
                let options = CmgOptions {
                    direct_threshold: 2,
                    max_levels,
                    max_hierarchy_nnz_factor: fill,
                    ..Default::default()
                };
                let pre = CmgPreconditioner::build(&graph, options).unwrap();
                let sum: usize = pre.hierarchy().report().matrix_nonzeros().iter().sum();
                if fill == 1.0 && max_levels == 16 {
                    assert_eq!(
                        pre.hierarchy().report().terminal_reason(),
                        TerminalReason::StagnatedFill
                    );
                    assert!(
                        sum > graph.matrix_nnz(),
                        "crossing level exceeds the old fill-only bound"
                    );
                }
                let estimate = CmgMemoryEstimate::conservative(
                    CmgProblemSize {
                        vertices: graph.vertex_count(),
                        input_edges: graph.edge_count(),
                        canonical_edges: graph.edge_count(),
                        right_hand_sides: 2,
                    },
                    options,
                    ParallelOptions {
                        threads: 2,
                        ..Default::default()
                    },
                )
                .unwrap();
                assert!(estimate.retained_solver_bytes() >= pre.retained_bytes());
                assert!(estimate.workspace_bytes_each() >= PcgWorkspace::new(&pre).byte_len());
                assert_eq!(estimate.complete_build_peak_bytes(), None);
                assert!(estimate.admit_complete_build(usize::MAX).is_err());
            }
        }
    }
}
