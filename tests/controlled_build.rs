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
