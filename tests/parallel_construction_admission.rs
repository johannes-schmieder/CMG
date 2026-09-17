//! Observe complete parallel-process allocation peaks without labelling Rayon
//! storage as CMG-owned. Failure injection is only at the two caller-side fixed
//! mapping banks, before dispatch, never in an infallible runtime allocation.
#![cfg(feature = "parallel")]
use cmg::*;
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::sync::atomic::{AtomicIsize, Ordering::SeqCst};

static LIVE: AtomicIsize = AtomicIsize::new(0);
static PEAK: AtomicIsize = AtomicIsize::new(0);
thread_local! {
    static CALLER_LIVE: Cell<isize> = const { Cell::new(0) };
    static CALLS: Cell<usize> = const { Cell::new(0) };
    static FAIL_SIZE: Cell<Option<usize>> = const { Cell::new(None) };
}
struct Observed;
fn fail(size: usize) -> bool {
    let _ = CALLS.try_with(|calls| calls.set(calls.get() + 1));
    FAIL_SIZE
        .try_with(|slot| {
            if slot.get() == Some(size) {
                slot.set(None);
                true
            } else {
                false
            }
        })
        .unwrap_or(false)
}
fn allocated(size: usize) {
    let live = LIVE.fetch_add(size as isize, SeqCst) + size as isize;
    PEAK.fetch_max(live, SeqCst);
    let _ = CALLER_LIVE.try_with(|v| v.set(v.get() + size as isize));
}
unsafe impl GlobalAlloc for Observed {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if fail(layout.size()) {
            return std::ptr::null_mut();
        }
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            allocated(layout.size());
        }
        ptr
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size() as isize, SeqCst);
        let _ = CALLER_LIVE.try_with(|v| v.set(v.get() - layout.size() as isize));
        unsafe {
            System.dealloc(ptr, layout);
        }
    }
    unsafe fn realloc(&self, ptr: *mut u8, old: Layout, size: usize) -> *mut u8 {
        if fail(size) {
            return std::ptr::null_mut();
        }
        let result = unsafe { System.realloc(ptr, old, size) };
        if !result.is_null() {
            // Conservative observation of old/new overlap, as in serial fixtures.
            allocated(size);
            LIVE.fetch_sub(old.size() as isize, SeqCst);
            let _ = CALLER_LIVE.try_with(|v| v.set(v.get() - old.size() as isize));
        }
        result
    }
}
#[global_allocator]
static ALLOCATOR: Observed = Observed;

fn report(roots: &[&dyn CmgStorage]) -> usize {
    let calls = CALLS.with(Cell::get);
    let bytes = CmgStorageReport::measure(roots)
        .unwrap()
        .total_bytes()
        .unwrap();
    assert_eq!(CALLS.with(Cell::get), calls);
    bytes
}

fn check(graph: &Laplacian, executor: &ParallelExecutor, options: CmgOptions) {
    let bound = CmgPreconditioner::executor_construction_cmg_allocation_bound(
        graph.vertex_count(),
        graph.edge_count(),
        options,
    )
    .unwrap();
    let calls = CALLS.with(Cell::get);
    assert!(matches!(
        CmgPreconditioner::build_with_executor_and_cmg_budget(graph, options, executor, bound - 1),
        Err(CmgError::MemoryBudgetExceeded { .. })
    ));
    assert_eq!(
        CALLS.with(Cell::get),
        calls,
        "short budget allocated before rejection"
    );
    let reference = CmgPreconditioner::build_with_executor(graph, options, executor).unwrap();
    let parents = report(&[graph, &reference]);
    let before = LIVE.load(SeqCst);
    PEAK.store(before, SeqCst);
    let pre =
        CmgPreconditioner::build_with_executor_and_cmg_budget(graph, options, executor, bound)
            .unwrap();
    let peak = (PEAK.load(SeqCst) - before).max(0);
    assert_eq!(pre, reference);
    let retained = report(&[graph, &reference, &pre]) - parents;
    assert!(retained <= bound);
    let hierarchy_bound = CmgHierarchy::executor_construction_cmg_allocation_bound(
        graph.vertex_count(),
        graph.edge_count(),
        options,
    )
    .unwrap();
    let calls = CALLS.with(Cell::get);
    assert!(
        CmgHierarchy::build_with_executor_and_cmg_budget(
            graph,
            options,
            executor,
            hierarchy_bound - 1
        )
        .is_err()
    );
    assert_eq!(CALLS.with(Cell::get), calls);
    // Compare the same construction stage. Preconditioner finalization replaces
    // the penultimate repeat using terminal-factor nonzeros; a standalone
    // hierarchy still uses coarse-graph nonzeros. The exact preconditioner
    // comparison above separately checks that finalization is preserved.
    assert_eq!(
        CmgHierarchy::build_with_executor_and_cmg_budget(graph, options, executor, hierarchy_bound)
            .unwrap(),
        CmgHierarchy::build_with_executor(graph, options, executor).unwrap()
    );
    println!(
        "PARALLEL_CMG_CONSTRUCTION_SUCCESS vertices={} edges={} workers={} cmg_bound={bound} retained_new={retained} observed_process_peak_increment={peak} runtime_included_in_observation=true runtime_admitted=false",
        graph.vertex_count(),
        graph.edge_count(),
        executor.thread_count()
    );

    // For these dense fixtures the first level necessarily contracts the 50
    // disconnected pairs. Both mapping banks allocate before any dispatch.
    if executor.thread_count() > 1 && graph.edge_count() >= 65_536 {
        for size in [
            std::mem::size_of::<Option<Edge>>(),
            std::mem::size_of::<Edge>(),
        ] {
            let before = CALLER_LIVE.with(Cell::get);
            FAIL_SIZE.with(|slot| slot.set(Some(size * graph.edge_count())));
            let result = CmgPreconditioner::build_with_executor_and_cmg_budget(
                graph, options, executor, bound,
            );
            let unconsumed = FAIL_SIZE.with(|slot| slot.replace(None));
            assert!(
                unconsumed.is_none(),
                "injection did not reach fixed mapping bank"
            );
            assert!(matches!(result, Err(CmgError::AllocationFailed { .. })));
            assert_eq!(CALLER_LIVE.with(Cell::get), before, "partial build leaked");
        }
    }
    #[cfg(feature = "experimental-components")]
    for prune in [false, true] {
        for preserve in [false, true] {
            for factor in [false, true] {
                let component = ComponentBuildOptions {
                    prune_coarse_isolates: prune,
                    preserve_unpruned_stopping: preserve,
                    factor_terminal_components: factor,
                };
                let bound =
                    CmgPreconditioner::component_executor_construction_cmg_allocation_bound(
                        graph.vertex_count(),
                        graph.edge_count(),
                        options,
                        component,
                    )
                    .unwrap();
                let calls = CALLS.with(Cell::get);
                assert!(
                    CmgPreconditioner::build_component_experiment_with_executor_and_cmg_budget(
                        graph,
                        options,
                        component,
                        executor,
                        bound - 1,
                    )
                    .is_err()
                );
                assert_eq!(CALLS.with(Cell::get), calls);
                let admitted =
                    CmgPreconditioner::build_component_experiment_with_executor_and_cmg_budget(
                        graph, options, component, executor, bound,
                    )
                    .unwrap();
                let legacy = CmgPreconditioner::build_component_experiment_with_executor(
                    graph, options, component, executor,
                )
                .unwrap();
                assert_eq!(admitted, legacy);
                let known: Vec<_> = (0..graph.vertex_count()).map(|v| (v % 13) as f64).collect();
                let rhs = graph.matvec(&known).unwrap();
                assert_eq!(admitted.apply(&rhs).unwrap(), legacy.apply(&rhs).unwrap());
                assert!(report(&[graph, &admitted]) - report(&[graph]) <= bound);
            }
        }
    }
}

#[test]
fn fixed_parallel_banks_preserve_thresholds_policies_and_cleanup() {
    let options = CmgOptions {
        direct_threshold: 64,
        max_levels: 8,
        ..Default::default()
    };
    for threads in [1, 4, 1] {
        let executor = ParallelExecutor::new(ParallelOptions {
            threads,
            min_parallel_len: 1,
            ..Default::default()
        })
        .unwrap();
        for width in [255, 256, 257] {
            let core = 2 * width;
            let graph = Laplacian::from_edges(
                core + 100,
                (0..width)
                    .flat_map(|u| (width..core).map(move |v| (u, v, 1.0)))
                    .chain((core..core + 100).step_by(2).map(|u| (u, u + 1, 0.5))),
            )
            .unwrap();
            check(&graph, &executor, options);
        }
        let endpoints: Vec<_> = (1..73).map(|v| (v - 1, v)).collect();
        let topology = PreparedLaplacianTopology::prepare(79, endpoints).unwrap();
        let mut workspace = topology.workspace().unwrap();
        for weight in [1.0, 0.125, 2.0] {
            let graph = topology
                .assemble_with_workspace(&vec![weight; 72], &mut workspace)
                .unwrap();
            check(&graph, &executor, options);
        }
        assert!(
            CmgStorageReport::measure(&[&executor])
                .unwrap()
                .runtime_heap_unbounded()
        );
        drop(executor); // Existing TLS lifetime tests verify the actual joins.
    }
    for (n, m) in [(usize::MAX, 0), (1, usize::MAX)] {
        assert!(CmgHierarchy::executor_construction_cmg_allocation_bound(n, m, options).is_err());
        assert!(
            CmgPreconditioner::executor_construction_cmg_allocation_bound(n, m, options).is_err()
        );
    }
    assert_eq!(ParallelExecutor::runtime_allocation_bound(), None);
    assert!(
        ParallelExecutor::new_with_memory_budget(ParallelOptions::default(), usize::MAX).is_err()
    );
}
