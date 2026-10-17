use super::*;
use cmg::{BuildBoundary, BuildDecision, BuildOperation, BuildStopReason};

pub(super) fn observe_allocation(bytes: usize) {
    if AUDIT.try_with(Cell::get).unwrap_or(false) {
        assert_eq!(PENDING.with(Cell::take), Some(bytes), "ungated allocation");
    }
}

fn gate(event: BuildBoundary) -> BuildDecision {
    assert_eq!(PENDING.with(Cell::get), None);
    if let BuildOperation::Allocation { bytes } = event.operation {
        if bytes != 0 {
            PENDING.with(|pending| pending.set(Some(bytes)));
        }
    }
    BuildDecision::Continue
}

fn charge(trace: &[BuildBoundary]) -> u64 {
    trace
        .iter()
        .map(|e| match e.operation {
            BuildOperation::Work { units } => u64::from(units),
            BuildOperation::Allocation { .. } => 0,
        })
        .sum()
}

fn pre(
    vertices: usize,
    connected: bool,
    threshold: usize,
    max_levels: usize,
) -> (Laplacian, CmgPreconditioner) {
    let graph = graph(vertices, connected);
    let cmg = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: threshold,
            max_levels,
            ..Default::default()
        },
    )
    .unwrap();
    (graph, cmg)
}

#[test]
fn requirements_match_actual_layouts_initialization_and_scalar_bits() {
    for (n, connected, threshold, levels) in [
        (0, true, 700, 128),
        (1, true, 700, 128),
        (12, true, 700, 128),
        (96, false, 4, 128),
        (96, false, 4, 1),
    ] {
        let (graph, cmg) = pre(n, connected, threshold, levels);
        for capacity in [1, 2, 4, 7] {
            let before = CALLS.with(Cell::get);
            let required =
                CmgBatchWorkspace::requirements_controlled(&cmg, capacity, u64::MAX, &mut |_| {
                    BuildDecision::Continue
                })
                .unwrap();
            assert_eq!(CALLS.with(Cell::get), before, "requirements allocated");
            assert_eq!(
                required.allocation_bytes,
                CmgBatchWorkspace::allocation_bytes(&cmg, capacity).unwrap()
            );
            let mut trace = Vec::new();
            let mut workspace = CmgBatchWorkspace::try_new_controlled(
                &cmg,
                capacity,
                required.allocation_bytes,
                required.initialization_work,
                &mut |event| {
                    trace.push(event);
                    BuildDecision::Continue
                },
            )
            .unwrap();
            assert_eq!(charge(&trace), required.initialization_work);
            assert_eq!(
                CmgStorageReport::measure(&[&workspace])
                    .unwrap()
                    .total_bytes()
                    .unwrap(),
                required.allocation_bytes
            );
            assert!(matches!(
                CmgBatchWorkspace::try_new_controlled(
                    &cmg,
                    capacity,
                    required.allocation_bytes,
                    required.initialization_work - 1,
                    &mut |_| BuildDecision::Continue
                ),
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    ..
                })
            ));
            let before = CALLS.with(Cell::get);
            assert!(matches!(
                CmgBatchWorkspace::try_new_controlled(
                    &cmg,
                    capacity,
                    required.allocation_bytes - 1,
                    u64::MAX,
                    &mut |_| BuildDecision::Continue
                ),
                Err(CmgError::MemoryBudgetExceeded { .. })
            ));
            assert_eq!(CALLS.with(Cell::get), before, "short admission allocated");
            let mut old =
                CmgBatchWorkspace::try_new(&cmg, capacity, required.allocation_bytes).unwrap();
            // Alternate full and narrow live prefixes, including short final
            // tiles and empty calls, to detect stale-lane reads on reuse.
            for columns in [capacity, 1, capacity.saturating_sub(1), 0, capacity] {
                let rhs = inputs(&graph, columns);
                let mut result = vec![f64::NAN; rhs.len()];
                let mut expected = vec![f64::NAN; rhs.len()];
                let before = CALLS.with(Cell::get);
                cmg.apply_compatible_batch_into(&rhs, &mut result, columns, &mut workspace)
                    .unwrap();
                cmg.apply_compatible_batch_into(&rhs, &mut expected, columns, &mut old)
                    .unwrap();
                assert_eq!(CALLS.with(Cell::get), before);
                assert_eq!(
                    result.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                    expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
                );
                for lane in 0..columns {
                    let input: Vec<_> = (0..n).map(|row| rhs[row * columns + lane]).collect();
                    let mut scalar = vec![0.; n];
                    cmg.apply_compatible_into(&input, &mut scalar, &mut cmg.workspace())
                        .unwrap();
                    for row in 0..n {
                        assert_eq!(
                            result[row * columns + lane].to_bits(),
                            scalar[row].to_bits()
                        );
                    }
                }
            }
        }
    }
}

#[test]
fn sizing_and_construction_stop_at_every_boundary_without_retained_allocations() {
    let (_, cmg) = pre(5, true, 3, 128);
    for capacity in [1, 4] {
        for sizing in [true, false] {
            let run = |work, control: &mut dyn cmg::BuildControl| -> Result<(), CmgError> {
                if sizing {
                    CmgBatchWorkspace::requirements_controlled(&cmg, capacity, work, control)
                        .map(|_| ())
                } else {
                    CmgBatchWorkspace::try_new_controlled(&cmg, capacity, usize::MAX, work, control)
                        .map(|_| ())
                }
            };
            let mut trace = Vec::new();
            run(u64::MAX, &mut |event| {
                trace.push(event);
                BuildDecision::Continue
            })
            .unwrap();
            for (index, expected) in trace.iter().enumerate() {
                for (decision, reason) in [
                    (BuildDecision::StopUnused, BuildStopReason::StopUnused),
                    (BuildDecision::Interrupted, BuildStopReason::Interrupted),
                ] {
                    let live = LIVE.with(Cell::get);
                    let mut seen = 0;
                    let result = run(u64::MAX, &mut |event| {
                        assert_eq!(event, trace[seen]);
                        seen += 1;
                        if seen == index + 1 {
                            decision
                        } else {
                            BuildDecision::Continue
                        }
                    });
                    assert_eq!(LIVE.with(Cell::get), live);
                    assert_eq!(seen, index + 1);
                    assert_eq!(
                        result.unwrap_err(),
                        CmgError::BuildStopped {
                            reason,
                            phase: expected.phase,
                            chunk_id: expected.chunk_id,
                            charged_work: expected.charged_work
                        }
                    );
                }
            }
            run(charge(&trace), &mut |_| BuildDecision::Continue).unwrap();
            assert!(matches!(
                run(charge(&trace) - 1, &mut |_| BuildDecision::Continue),
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    ..
                })
            ));
            assert!(matches!(
                run(0, &mut |_| panic!("budget must precede callback")),
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::WorkBudgetExhausted,
                    ..
                })
            ));
        }
    }
}

#[test]
fn layouts_are_gated_and_each_allocation_failure_releases_partial_workspace() {
    let (_, cmg) = pre(96, false, 4, 128);
    for capacity in [1, 4] {
        let required =
            CmgBatchWorkspace::requirements_controlled(&cmg, capacity, u64::MAX, &mut |_| {
                BuildDecision::Continue
            })
            .unwrap();
        let before = CALLS.with(Cell::get);
        let live = LIVE.with(Cell::get);
        AUDIT.with(|audit| audit.set(true));
        let result = CmgBatchWorkspace::try_new_controlled(
            &cmg,
            capacity,
            required.allocation_bytes,
            required.initialization_work,
            &mut gate,
        );
        AUDIT.with(|audit| audit.set(false));
        let calls = CALLS.with(Cell::get) - before;
        assert_eq!(PENDING.with(Cell::get), None);
        let workspace = result.unwrap();
        assert_eq!(
            LIVE.with(Cell::get) - live,
            required.allocation_bytes as isize
        );
        drop(workspace);
        assert_eq!(LIVE.with(Cell::get), live);
        for fail_at in 0..calls {
            AUDIT.with(|audit| audit.set(true));
            FAIL.with(|fail| fail.set(Some(fail_at)));
            let result = CmgBatchWorkspace::try_new_controlled(
                &cmg,
                capacity,
                required.allocation_bytes,
                required.initialization_work,
                &mut gate,
            );
            FAIL.with(|fail| fail.set(None));
            AUDIT.with(|audit| audit.set(false));
            assert_eq!(PENDING.with(Cell::get), None);
            assert!(matches!(result, Err(CmgError::AllocationFailed { .. })));
            assert_eq!(LIVE.with(Cell::get), live);
        }
    }
}

#[test]
fn invalid_capacity_and_checked_overflow_precede_allocation() {
    let (_, cmg) = pre(12, true, 700, 128);
    for capacity in [0, usize::MAX] {
        let before = CALLS.with(Cell::get);
        let query =
            CmgBatchWorkspace::requirements_controlled(&cmg, capacity, u64::MAX, &mut |_| {
                BuildDecision::Continue
            });
        let build = CmgBatchWorkspace::try_new_controlled(
            &cmg,
            capacity,
            usize::MAX,
            u64::MAX,
            &mut |_| BuildDecision::Continue,
        );
        assert_eq!(CALLS.with(Cell::get), before);
        if capacity == 0 {
            assert!(matches!(
                query,
                Err(CmgError::InvalidOption {
                    name: "CMG batch capacity",
                    ..
                })
            ));
            assert!(matches!(
                build,
                Err(CmgError::InvalidOption {
                    name: "CMG batch capacity",
                    ..
                })
            ));
        } else {
            assert!(matches!(
                query,
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::CounterOverflow,
                    ..
                })
            ));
            assert!(matches!(
                build,
                Err(CmgError::BuildStopped {
                    reason: BuildStopReason::CounterOverflow,
                    ..
                })
            ));
        }
    }
}
