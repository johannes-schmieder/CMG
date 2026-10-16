#![cfg(feature = "experimental-components")]

use cmg::{
    BuildBoundary, BuildDecision, BuildOperation, BuildStopReason, CmgError, CmgOptions,
    CmgPreconditioner, ComponentBuildOptions, Laplacian,
};

fn graph(n: usize) -> Laplacian {
    let mut edges = Vec::new();
    for i in 0..n.saturating_sub(1) {
        if i % 29 != 28 {
            edges.push((i, i + 1, (i % 7 + 1) as f64));
        }
        if i + 3 < n && i % 29 < 25 {
            edges.push((i, i + 3, 0.125));
        }
    }
    Laplacian::from_edges(n, edges).unwrap()
}

#[test]
fn unlimited_control_preserves_hierarchy_factor_and_cycle_bits() {
    for n in [0, 1, 2, 9, 65, 257] {
        let graph = graph(n);
        for prune in [false, true] {
            for preserve in [false, true] {
                for factor in [false, true] {
                    let experiment = ComponentBuildOptions {
                        prune_coarse_isolates: prune,
                        preserve_unpruned_stopping: preserve,
                        factor_terminal_components: factor,
                    };
                    let options = CmgOptions {
                        direct_threshold: 8,
                        ..CmgOptions::default()
                    };
                    let old = CmgPreconditioner::build_component_experiment_serial_with_budget(
                        &graph,
                        options,
                        experiment,
                        usize::MAX,
                    )
                    .unwrap();
                    let mut boundaries = Vec::new();
                    let new = CmgPreconditioner::build_component_experiment_serial_controlled(
                        &graph,
                        options,
                        experiment,
                        usize::MAX,
                        u64::MAX,
                        &mut |event| {
                            boundaries.push(event);
                            BuildDecision::Continue
                        },
                    )
                    .unwrap();
                    assert_eq!(old.hierarchy(), new.hierarchy());
                    assert_eq!(old.terminal_factor(), new.terminal_factor());
                    assert_eq!(old.repeat_counts(), new.repeat_counts());
                    let probe: Vec<_> = (0..n)
                        .map(|i| ((i * 11 % 17) as f64 - 8.0) * 0.25)
                        .collect();
                    let rhs = graph.matvec(&probe).unwrap();
                    let old_result = old.apply(&rhs).unwrap();
                    let new_result = new.apply(&rhs).unwrap();
                    assert_eq!(
                        old_result.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                        new_result.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
                    );
                    let mut second = Vec::new();
                    CmgPreconditioner::build_component_experiment_serial_controlled(
                        &graph,
                        options,
                        experiment,
                        usize::MAX,
                        u64::MAX,
                        &mut |event| {
                            second.push(event);
                            BuildDecision::Continue
                        },
                    )
                    .unwrap();
                    assert_eq!(boundaries, second);
                    assert!(boundaries.iter().all(|e| match e.operation {
                        BuildOperation::Work { units } => (1..=1024).contains(&units),
                        _ => true,
                    }));
                }
            }
        }
    }
}

#[test]
fn full_candidate_denies_every_boundary_and_recovers() {
    let graph = graph(5);
    let options = CmgOptions {
        direct_threshold: 3,
        ..CmgOptions::default()
    };
    let experiment = ComponentBuildOptions {
        prune_coarse_isolates: true,
        preserve_unpruned_stopping: true,
        factor_terminal_components: true,
    };
    let mut trace: Vec<BuildBoundary> = Vec::new();
    let build = |budget, callback: &mut dyn cmg::BuildControl| {
        CmgPreconditioner::build_component_experiment_serial_controlled(
            &graph,
            options,
            experiment,
            usize::MAX,
            budget,
            callback,
        )
    };
    build(u64::MAX, &mut |event| {
        trace.push(event);
        BuildDecision::Continue
    })
    .unwrap();
    for stop in 0..trace.len() {
        for (decision, reason) in [
            (BuildDecision::StopUnused, BuildStopReason::StopUnused),
            (BuildDecision::Interrupted, BuildStopReason::Interrupted),
        ] {
            let mut seen = 0;
            let result = build(u64::MAX, &mut |event| {
                assert_eq!(event, trace[seen]);
                let result = if seen == stop {
                    decision
                } else {
                    BuildDecision::Continue
                };
                seen += 1;
                result
            });
            assert!(
                matches!(result,Err(CmgError::BuildStopped {reason:r,chunk_id,charged_work,..}) if r==reason && chunk_id==stop as u64 && charged_work==trace[stop].charged_work)
            );
            assert_eq!(seen, stop + 1);
        }
    }
    let work: u64 = trace
        .iter()
        .map(|event| match event.operation {
            BuildOperation::Work { units } => u64::from(units),
            _ => 0,
        })
        .sum();
    build(work, &mut |_| BuildDecision::Continue).unwrap();
    assert!(matches!(
        build(work - 1, &mut |_| BuildDecision::Continue),
        Err(CmgError::BuildStopped {
            reason: BuildStopReason::WorkBudgetExhausted,
            ..
        })
    ));
    assert!(matches!(
        build(0, &mut |_| panic!("zero budget must deny before callback")),
        Err(CmgError::BuildStopped {
            reason: BuildStopReason::WorkBudgetExhausted,
            ..
        })
    ));
}

#[test]
fn callback_can_park_then_grant_or_drain_without_building_a_pool() {
    for decision in [
        BuildDecision::Continue,
        BuildDecision::StopUnused,
        BuildDecision::Interrupted,
    ] {
        let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(0);
        let (grant_tx, grant_rx) = std::sync::mpsc::sync_channel(0);
        let worker = std::thread::spawn(move || {
            let graph = graph(5);
            let mut first = true;
            CmgPreconditioner::build_component_experiment_serial_controlled(
                &graph,
                CmgOptions::default(),
                ComponentBuildOptions::default(),
                usize::MAX,
                u64::MAX,
                &mut |_| {
                    if first {
                        first = false;
                        ready_tx.send(()).unwrap();
                        grant_rx.recv().unwrap()
                    } else {
                        BuildDecision::Continue
                    }
                },
            )
        });
        ready_rx.recv().unwrap();
        grant_tx.send(decision).unwrap();
        let result = worker.join().unwrap();
        match decision {
            BuildDecision::Continue => assert!(result.is_ok()),
            _ => assert!(matches!(result, Err(CmgError::BuildStopped { .. }))),
        }
    }
}

#[test]
fn prefix_parking_preserves_full_construction_and_global_trace() {
    use cmg::{BuildPrefix, PrefixDecision, PrefixOutcome};
    let edges: Vec<_> = (0..16).map(|i| (i, i + 1, 1.0 + (i % 3) as f64)).collect();
    let construct = |control: &mut dyn cmg::BuildControl| {
        let graph = Laplacian::from_edges_controlled(17, &edges, usize::MAX, u64::MAX, control)?;
        let built = CmgPreconditioner::build_component_experiment_serial_controlled(
            &graph,
            CmgOptions {
                direct_threshold: 3,
                ..Default::default()
            },
            ComponentBuildOptions::default(),
            usize::MAX,
            u64::MAX,
            control,
        )?;
        built.try_workspace_controlled(u64::MAX, control)?;
        Ok::<_, CmgError>((graph, built))
    };
    let mut reference = BuildPrefix::new(11, u64::MAX);
    reference.grant(u64::MAX).unwrap();
    let mut expected = Vec::new();
    let (graph, old) = construct(&mut |boundary| {
        assert_eq!(reference.admit(boundary).unwrap(), PrefixDecision::Continue);
        expected.push(reference.snapshot().last_admitted.unwrap());
        BuildDecision::Continue
    })
    .unwrap();
    let cap = reference.snapshot().charged;
    for quantum in [257, 4096] {
        let (park_tx, park_rx) = std::sync::mpsc::sync_channel(0);
        let (grant_tx, grant_rx) = std::sync::mpsc::sync_channel(0);
        let (new, actual) = std::thread::scope(|scope| {
            let worker = scope.spawn(move || {
                let mut prefix = BuildPrefix::new(11, cap);
                let mut actual = Vec::new();
                let (_, built) = construct(&mut |boundary| loop {
                    match prefix.admit(boundary).unwrap() {
                        PrefixDecision::Continue => {
                            actual.push(prefix.snapshot().last_admitted.unwrap());
                            break BuildDecision::Continue;
                        }
                        PrefixDecision::Park => {
                            prefix.drain_issued_prefix().unwrap();
                            park_tx.send(Some(prefix.snapshot())).unwrap();
                            prefix.grant(grant_rx.recv().unwrap()).unwrap();
                        }
                        other => panic!("unexpected decision: {other:?}"),
                    }
                })
                .unwrap();
                prefix.finish(PrefixOutcome::Complete).unwrap();
                park_tx.send(None).unwrap();
                (built, actual)
            });
            while let Some(parked) = park_rx.recv().unwrap() {
                assert!(parked.charged <= parked.issued);
                assert!(parked.drained());
                let next = parked.issued.saturating_add(quantum).min(cap);
                grant_tx.send(next).unwrap();
            }
            worker.join().unwrap()
        });
        assert_eq!(actual, expected);
        assert_eq!(new.hierarchy(), old.hierarchy());
        assert_eq!(new.terminal_factor(), old.terminal_factor());
        let rhs = graph
            .matvec(&(0..17).map(|i| i as f64).collect::<Vec<_>>())
            .unwrap();
        assert_eq!(new.apply(&rhs).unwrap(), old.apply(&rhs).unwrap());
    }
}

#[test]
fn grouped_dense_work_preserves_bits_tariffs_and_prepaid_stops() {
    struct Control {
        grouped: bool,
        stop: usize,
        trace: Vec<BuildBoundary>,
    }
    impl cmg::BuildControl for Control {
        fn group_dense_work(&self) -> bool {
            self.grouped
        }
        fn checkpoint(&mut self, boundary: BuildBoundary) -> BuildDecision {
            self.trace.push(boundary);
            if self.trace.len() - 1 == self.stop {
                BuildDecision::StopUnused
            } else {
                BuildDecision::Continue
            }
        }
    }
    let graph = graph(65);
    let build = |budget, control: &mut Control| {
        CmgPreconditioner::build_component_experiment_serial_controlled(
            &graph,
            CmgOptions::default(),
            ComponentBuildOptions::default(),
            usize::MAX,
            budget,
            control,
        )
    };
    let mut plain = Control {
        grouped: false,
        stop: usize::MAX,
        trace: vec![],
    };
    let mut grouped = Control {
        grouped: true,
        stop: usize::MAX,
        trace: vec![],
    };
    let old = build(u64::MAX, &mut plain).unwrap();
    let new = build(u64::MAX, &mut grouped).unwrap();
    assert_eq!(old.hierarchy(), new.hierarchy());
    assert_eq!(old.terminal_factor(), new.terminal_factor());
    assert_eq!(old.repeat_counts(), new.repeat_counts());
    let rhs = graph
        .matvec(&(0..65).map(|i| (i % 7) as f64).collect::<Vec<_>>())
        .unwrap();
    assert_eq!(
        old.apply(&rhs)
            .unwrap()
            .iter()
            .map(|v| v.to_bits())
            .collect::<Vec<_>>(),
        new.apply(&rhs)
            .unwrap()
            .iter()
            .map(|v| v.to_bits())
            .collect::<Vec<_>>()
    );
    let work = |trace: &[BuildBoundary]| {
        trace
            .iter()
            .map(|b| match b.operation {
                BuildOperation::Work { units } => u64::from(units),
                _ => 0,
            })
            .sum::<u64>()
    };
    assert_eq!(work(&plain.trace), work(&grouped.trace));
    assert!(grouped.trace.len() < plain.trace.len());
    assert!(grouped.trace.iter().all(|b| match b.operation {
        BuildOperation::Work { units } => (1..=1024).contains(&units),
        _ => true,
    }));
    // Stop before the first, middle and final eight-term groups; a stopped group
    // never publishes a partial factor. Existing tests cover every primitive gate.
    let groups: Vec<_> = grouped
        .trace
        .iter()
        .enumerate()
        .filter(|(_, b)| {
            b.phase == "terminal dense numeric"
                && b.operation == BuildOperation::Work { units: 1024 }
        })
        .map(|(i, _)| i)
        .collect();
    assert!(groups.len() > 3);
    for stop in [groups[0], groups[groups.len() / 2], *groups.last().unwrap()] {
        let mut c = Control {
            grouped: true,
            stop,
            trace: vec![],
        };
        assert!(matches!(
            build(u64::MAX, &mut c),
            Err(CmgError::BuildStopped {
                reason: BuildStopReason::StopUnused,
                ..
            })
        ));
        assert_eq!(c.trace, grouped.trace[..=stop]);
    }
    let mut c = Control {
        grouped: true,
        stop: usize::MAX,
        trace: vec![],
    };
    assert!(matches!(
        build(work(&grouped.trace) - 1, &mut c),
        Err(CmgError::BuildStopped {
            reason: BuildStopReason::WorkBudgetExhausted,
            ..
        })
    ));
    c.trace.clear();
    build(work(&grouped.trace), &mut c).unwrap();
    assert_eq!(c.trace, grouped.trace);
}
