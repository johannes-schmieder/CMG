//! Serial construction admission: independent requested-layout observation.
use cmg::*;
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

thread_local! {
    static LIVE: Cell<isize> = const { Cell::new(0) };
    static PEAK: Cell<isize> = const { Cell::new(0) };
    static CALLS: Cell<usize> = const { Cell::new(0) };
    static FALLIBLE: Cell<usize> = const { Cell::new(0) };
    static FAIL: Cell<Option<usize>> = const { Cell::new(None) };
}
struct Observed;
fn allocated(bytes: usize) {
    let _ = LIVE.try_with(|live| {
        live.set(live.get() + bytes as isize);
        let _ = PEAK.try_with(|peak| peak.set(peak.get().max(live.get())));
    });
}
fn fail(size: usize) -> bool {
    let _ = CALLS.try_with(|calls| calls.set(calls.get() + 1));
    // Arc::new is infallible on stable Rust. Do not inject at Arc layout sizes
    // (including vector requests coincidentally equal to those sizes). This
    // intentionally does NOT claim all-allocation failure coverage. Terminal
    // factors, which own no Arcs, have exhaustive injection in a separate test.
    let counts = 2 * std::mem::size_of::<usize>();
    if [
        counts,
        counts + std::mem::size_of::<Vec<f64>>(),
        counts + std::mem::size_of::<Components>(),
    ]
    .contains(&size)
    {
        return false;
    }
    let _ = FALLIBLE.try_with(|calls| calls.set(calls.get() + 1));
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
        let _ = LIVE.try_with(|live| live.set(live.get() - layout.size() as isize));
        unsafe { System.dealloc(ptr, layout) };
    }
    unsafe fn realloc(&self, ptr: *mut u8, old: Layout, size: usize) -> *mut u8 {
        if fail(size) {
            return std::ptr::null_mut();
        }
        let ptr = unsafe { System.realloc(ptr, old, size) };
        if !ptr.is_null() {
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
    let value = CmgStorageReport::measure(roots)
        .unwrap()
        .total_bytes()
        .unwrap();
    assert_eq!(CALLS.with(Cell::get), calls);
    value
}

fn check_hierarchy(graph: &Laplacian, options: CmgOptions) {
    let reference = CmgHierarchy::build(graph, options).unwrap();
    let bound = CmgHierarchy::serial_construction_allocation_bound(
        graph.vertex_count(),
        graph.edge_count(),
        options,
    )
    .unwrap();
    let before = live();
    let calls = CALLS.with(Cell::get);
    assert!(CmgHierarchy::build_serial_with_budget(graph, options, bound - 1).is_err());
    assert_eq!(CALLS.with(Cell::get), calls);
    PEAK.with(|peak| peak.set(before));
    let hierarchy = CmgHierarchy::build_serial_with_budget(graph, options, bound).unwrap();
    let peak = (PEAK.with(Cell::get) - before) as usize;
    assert!(peak <= bound, "standalone hierarchy {peak} > {bound}");
    assert_eq!(hierarchy, reference);
    assert_eq!(
        report(&[graph, &hierarchy]) - report(&[graph]),
        (live() - before) as usize
    );
    drop(hierarchy);
    assert_eq!(live(), before);
    println!(
        "SERIAL_HIERARCHY_ALLOCATION_SUCCESS vertices={} edges={} bound={bound} peak={peak}",
        graph.vertex_count(),
        graph.edge_count()
    );
}

fn check(
    graph: &Laplacian,
    options: CmgOptions,
    component: Option<(bool, bool, bool)>,
    inject: bool,
) -> TerminalReason {
    #[cfg(feature = "experimental-components")]
    let experiment = component.map(|(prune, preserve, factor)| ComponentBuildOptions {
        prune_coarse_isolates: prune,
        preserve_unpruned_stopping: preserve,
        factor_terminal_components: factor,
    });
    let bound = if component.is_some() {
        #[cfg(feature = "experimental-components")]
        {
            CmgPreconditioner::component_serial_construction_allocation_bound(
                graph.vertex_count(),
                graph.edge_count(),
                options,
                experiment.unwrap(),
            )
            .unwrap()
        }
        #[cfg(not(feature = "experimental-components"))]
        unreachable!()
    } else {
        CmgPreconditioner::serial_construction_allocation_bound(
            graph.vertex_count(),
            graph.edge_count(),
            options,
        )
        .unwrap()
    };
    let reference = if component.is_some() {
        #[cfg(feature = "experimental-components")]
        {
            CmgPreconditioner::build_component_experiment(graph, options, experiment.unwrap())
                .unwrap()
        }
        #[cfg(not(feature = "experimental-components"))]
        unreachable!()
    } else {
        CmgPreconditioner::build(graph, options).unwrap()
    };
    let construct = |budget| {
        if component.is_some() {
            #[cfg(feature = "experimental-components")]
            {
                CmgPreconditioner::build_component_experiment_serial_with_budget(
                    graph,
                    options,
                    experiment.unwrap(),
                    budget,
                )
            }
            #[cfg(not(feature = "experimental-components"))]
            unreachable!()
        } else {
            CmgPreconditioner::build_serial_with_budget(graph, options, budget)
        }
    };
    let before = live();
    let calls = CALLS.with(Cell::get);
    let fallible = FALLIBLE.with(Cell::get);
    assert!(construct(bound - 1).is_err());
    assert_eq!(CALLS.with(Cell::get), calls, "short budget allocated");
    PEAK.with(|peak| peak.set(before));
    let pre = construct(bound).unwrap();
    let allocations = FALLIBLE.with(Cell::get) - fallible;
    let peak = (PEAK.with(Cell::get) - before) as usize;
    assert!(peak <= bound, "{peak} > {bound}");
    assert_eq!(
        pre, reference,
        "admission changed numerical state or stopping"
    );
    assert_eq!(
        report(&[graph, &pre]) - report(&[graph]),
        (live() - before) as usize
    );
    let reason = pre.hierarchy().report().terminal_reason();
    let old_and_parent = report(&[graph, &pre]);
    let before_second = live();
    let second = construct(bound).unwrap();
    assert_eq!(
        report(&[graph, &pre, &second]) - old_and_parent,
        (live() - before_second) as usize
    );
    drop((pre, second));
    assert_eq!(live(), before);
    if inject {
        for index in 0..allocations {
            FAIL.with(|fail| fail.set(Some(index)));
            let result = construct(bound);
            FAIL.with(|fail| fail.set(None));
            assert!(
                matches!(result, Err(CmgError::AllocationFailed { .. })),
                "failure index {index}"
            );
            assert_eq!(live(), before, "partial hierarchy leaked at {index}");
        }
    }
    println!(
        "SERIAL_BUILD_ALLOCATION_SUCCESS vertices={} edges={} component={component:?} reason={reason:?} bound={bound} peak={peak} injected={} arc_size_injection_excluded=true",
        graph.vertex_count(),
        graph.edge_count(),
        if inject { allocations } else { 0 }
    );
    reason
}

#[test]
fn serial_hierarchy_bounds_preserve_all_stopping_and_component_policies() {
    let fixtures = [
        Laplacian::from_edges(0, []).unwrap(),
        Laplacian::from_edges(17, []).unwrap(),
        Laplacian::from_edges(2, [(0, 1, 1.0)]).unwrap(),
        Laplacian::from_edges(73, (1..73).map(|v| (v - 1, v, 1.0))).unwrap(),
        Laplacian::from_edges(28, (0..28).map(|v| (v, (v + 1) % 28, 1.0))).unwrap(),
        Laplacian::from_edges(20, (0..20).flat_map(|v| (0..v).map(move |u| (u, v, 1.0)))).unwrap(),
        Laplacian::from_edges(
            133,
            (1..73)
                .map(|v| (v - 1, v, if v == 36 { 1.0 / 65536.0 } else { 1.0 }))
                .chain((73..113).step_by(2).map(|v| (v, v + 1, 2.0))),
        )
        .unwrap(),
    ];
    let mut seen = [false; 5];
    for (index, graph) in fixtures.iter().enumerate() {
        for (threshold, levels, fill) in [
            (1, 8, 5.0),
            (16, 8, 5.0),
            (74, 8, 5.0),
            (1, 1, 5.0),
            (1, 8, 0.01),
            (1, 8, 1.0),
        ] {
            let options = CmgOptions {
                direct_threshold: threshold,
                max_levels: levels,
                max_hierarchy_nnz_factor: fill,
                ..Default::default()
            };
            check_hierarchy(graph, options);
            let reason = check(
                graph,
                options,
                None,
                (index == 3 || index == 5) && threshold == 16,
            );
            seen[match reason {
                TerminalReason::Direct => 0,
                TerminalReason::FullContraction => 1,
                TerminalReason::StagnatedVertexReduction => 2,
                TerminalReason::StagnatedFill => 3,
                TerminalReason::MaximumLevels => 4,
            }] = true;
            #[cfg(feature = "experimental-components")]
            for prune in [false, true] {
                for preserve in [false, true] {
                    for factor in [false, true] {
                        check(
                            graph,
                            options,
                            Some((prune, preserve, factor)),
                            index == 6 && threshold == 16 && preserve && prune,
                        );
                    }
                }
            }
        }
    }
    assert!(seen.into_iter().all(|present| present));
    let options = CmgOptions::default();
    assert!(
        CmgPreconditioner::serial_construction_allocation_bound(usize::MAX, 0, options).is_err()
    );
    assert!(
        CmgPreconditioner::serial_construction_allocation_bound(1, usize::MAX, options).is_err()
    );
    assert!(
        CmgPreconditioner::serial_construction_allocation_bound(
            1,
            0,
            CmgOptions {
                max_levels: usize::MAX,
                ..options
            }
        )
        .is_err()
    );
}

#[test]
fn prepared_numeric_generations_share_parents_but_admit_new_builds() {
    let endpoints: Vec<_> = (1..41).map(|v| (v - 1, v)).collect();
    let topology = PreparedLaplacianTopology::prepare(49, endpoints).unwrap();
    let mut workspace = topology.workspace().unwrap();
    for weight in [1.0, 0.125, 2.0] {
        let graph = topology
            .assemble_with_workspace(&[weight; 40], &mut workspace)
            .unwrap();
        let options = CmgOptions {
            direct_threshold: 12,
            max_levels: 6,
            ..Default::default()
        };
        check(&graph, options, None, false);
        #[cfg(feature = "experimental-components")]
        check(&graph, options, Some((true, true, true)), false);
    }
}
