#![cfg(all(feature = "parallel", feature = "experimental-components"))]
use cmg::*;

const COMPONENT: ComponentBuildOptions = ComponentBuildOptions {
    prune_coarse_isolates: true,
    preserve_unpruned_stopping: true,
    factor_terminal_components: true,
};

#[test]
fn executor_component_setup_matches_serial_including_parallel_sort_boundary() {
    let dense = Laplacian::from_edges(
        612,
        (0..256)
            .flat_map(|u| (256..512).map(move |v| (u, v, 1.0)))
            .chain((512..562).step_by(2).map(|u| (u, u + 1, 0.5))),
    )
    .unwrap();
    assert!(dense.edge_count() >= 65_536);
    for threads in [1, 4] {
        let executor = ParallelExecutor::new(ParallelOptions {
            threads,
            min_parallel_len: 1,
            ..Default::default()
        })
        .unwrap();
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
                        ..Default::default()
                    };
                    let serial =
                        CmgPreconditioner::build_component_experiment(&dense, options, experiment)
                            .unwrap();
                    let parallel = CmgPreconditioner::build_component_experiment_with_executor(
                        &dense, options, experiment, &executor,
                    )
                    .unwrap();
                    assert_eq!(serial, parallel);
                    let rhs = dense
                        .matvec(&(0..612).map(|v| (v % 13) as f64).collect::<Vec<_>>())
                        .unwrap();
                    assert_eq!(serial.apply(&rhs).unwrap(), parallel.apply(&rhs).unwrap());
                }
            }
        }
        assert!(
            CmgStorageReport::measure(&[&executor])
                .unwrap()
                .runtime_heap_unbounded()
        );
    }
    assert!(
        ParallelExecutor::new_with_memory_budget(ParallelOptions::default(), usize::MAX).is_err()
    );
}

#[test]
fn prepared_component_banks_cover_serial_planned_and_concurrent_rhs() {
    // A material path, many pairs, isolates, a cycle and a tiny weighted bridge.
    let mut endpoints: Vec<_> = (1..64).map(|i| (i - 1, i)).collect();
    endpoints.push((0, 63));
    endpoints.extend((64..104).step_by(2).map(|i| (i, i + 1)));
    let topology =
        PreparedLaplacianTopology::prepare_with_budget(112, &endpoints, usize::MAX).unwrap();
    let mut weights = vec![1.0; endpoints.len()];
    weights[31] = 1e-9;
    // The alternate route around the cycle keeps this case well-conditioned;
    // weak-bridge forward-accuracy coverage remains in adversarial/oracle tests.
    let first = topology.assemble(&weights).unwrap();
    let mut target: Vec<_> = (0..112).map(|i| ((i * 7) % 19) as f64).collect();
    topology.components().center_in_place(&mut target).unwrap();
    for threads in [1, 4] {
        let executor = ParallelExecutor::new(ParallelOptions {
            threads,
            min_parallel_len: 1,
            reduction_chunk_size: 16,
            ..Default::default()
        })
        .unwrap();
        let pre = CmgPreconditioner::build_component_experiment_with_executor(
            &first,
            CmgOptions {
                direct_threshold: 8,
                ..Default::default()
            },
            COMPONENT,
            &executor,
        )
        .unwrap();
        let plan = ParallelCmgPlan::build(&pre, &executor).unwrap();
        let bank_bytes = PcgBatchWorkspace::bank_allocation_bytes(&pre, threads).unwrap();
        let mut bank = PcgBatchWorkspace::new_bank_with_budget(&pre, threads, bank_bytes).unwrap();
        let before = CmgStorageReport::measure(&[&bank]).unwrap();
        for scale in [0.5, 1.0, 2.0] {
            let graph = topology
                .assemble(&weights.iter().map(|w| scale * w).collect::<Vec<_>>())
                .unwrap();
            let rhs = graph.matvec(&target).unwrap();
            let packed: Vec<_> = (0..4).flat_map(|_| rhs.iter().copied()).collect();
            let mut output = vec![f64::NAN; packed.len()];
            let mut diagnostic = [PcgDiagnostics::default(); 4];
            for planned in [false, true] {
                let rhs_view = PcgBatchRef::contiguous(&packed, 4, 112).unwrap();
                let out_view = PcgBatchMut::contiguous(&mut output, 4, 112).unwrap();
                let options = PcgOptions {
                    relative_tolerance: 1e-12,
                    ..Default::default()
                };
                if planned {
                    solve_pcg_batch_with_plan_and_retained_preconditioner_into_with_workspace(
                        &graph,
                        &pre,
                        &plan,
                        rhs_view,
                        None,
                        out_view,
                        &mut diagnostic,
                        options,
                        &mut bank[0],
                        &executor,
                    )
                    .unwrap();
                } else {
                    solve_pcg_batch_with_retained_preconditioner_into_with_executor(
                        &graph,
                        &pre,
                        rhs_view,
                        None,
                        out_view,
                        &mut diagnostic,
                        options,
                        &mut bank,
                        &executor,
                    )
                    .unwrap();
                }
                for (x, d) in output.chunks(112).zip(&diagnostic) {
                    assert!(x.iter().zip(&target).all(|(a, b)| (a - b).abs() < 1e-6));
                    let residual = graph
                        .matvec(x)
                        .unwrap()
                        .iter()
                        .zip(&rhs)
                        .map(|(ax, b)| (b - ax).powi(2))
                        .sum::<f64>()
                        .sqrt();
                    assert!(residual <= d.tolerance());
                }
                assert_eq!(CmgStorageReport::measure(&[&bank]).unwrap(), before);
            }
        }
    }
}
