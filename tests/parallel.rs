#![cfg(feature = "parallel")]

use cmg::{
    Aggregation, CmgError, CmgHierarchy, CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan,
    ParallelExecutor, ParallelOptions, PcgOptions, PcgWorkspace, build_forest_grouping,
    build_forest_grouping_with_executor, maximum_weight_forest,
    maximum_weight_forest_with_executor, solve_pcg_batch, solve_pcg_batch_with_executor,
    solve_pcg_with_plan_and_workspace, solve_pcg_with_workspace,
};

fn worker_firm_problem(per_side: usize) -> (Laplacian, Vec<f64>) {
    let firm_offset = per_side;
    let mut edges = Vec::with_capacity(3 * per_side);
    for worker in 0..per_side {
        edges.push((worker, firm_offset + worker, 1.0));
        edges.push((worker, firm_offset + (worker + 1) % per_side, 0.75));
        edges.push((worker, firm_offset + (5 * worker + 17) % per_side, 1.25));
    }
    let graph = Laplacian::from_edges(2 * per_side, edges).unwrap();
    let target: Vec<f64> = (0..2 * per_side)
        .map(|vertex| (vertex % 127) as f64 / 23.0 - 2.0)
        .collect();
    let rhs = graph.matvec(&target).unwrap();
    (graph, rhs)
}

fn path_problem(vertex_count: usize, rhs_count: usize) -> (Laplacian, Vec<Vec<f64>>) {
    let graph = Laplacian::from_edges(
        vertex_count,
        (0..vertex_count - 1)
            .map(|vertex| (vertex, vertex + 1, 0.75 + (vertex % 19) as f64 / 13.0)),
    )
    .unwrap();
    let right_hand_sides = (0..rhs_count)
        .map(|rhs_index| {
            let target: Vec<f64> = (0..vertex_count)
                .map(|vertex| ((vertex * 31 + rhs_index * 17) % 101) as f64 - 50.0)
                .collect();
            graph.matvec(&target).unwrap()
        })
        .collect();
    (graph, right_hand_sides)
}

#[test]
fn parallel_batch_matches_serial_results_and_input_order() {
    let (graph, right_hand_sides) = path_problem(2_000, 12);
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let options = PcgOptions::default();
    let serial = solve_pcg_batch(&graph, &preconditioner, &right_hand_sides, options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        workspace_memory_budget_bytes: None,
        ..ParallelOptions::default()
    })
    .unwrap();
    let parallel = solve_pcg_batch_with_executor(
        &graph,
        &preconditioner,
        &right_hand_sides,
        options,
        &executor,
    )
    .unwrap();

    assert_eq!(serial, parallel);
}

#[test]
fn workspace_budget_limits_concurrency_and_rejects_too_small_budget() {
    let (graph, right_hand_sides) = path_problem(1_000, 8);
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let workspace_bytes = PcgWorkspace::new(&preconditioner).byte_len();

    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 8,
        workspace_memory_budget_bytes: Some(workspace_bytes.saturating_mul(2)),
        ..ParallelOptions::default()
    })
    .unwrap();
    assert_eq!(executor.batch_concurrency(workspace_bytes, 8).unwrap(), 2);
    let results = solve_pcg_batch_with_executor(
        &graph,
        &preconditioner,
        &right_hand_sides,
        PcgOptions::default(),
        &executor,
    )
    .unwrap();
    assert_eq!(results.len(), right_hand_sides.len());

    let too_small = ParallelExecutor::new(ParallelOptions {
        threads: 8,
        workspace_memory_budget_bytes: Some(workspace_bytes - 1),
        ..ParallelOptions::default()
    })
    .unwrap();
    let error = solve_pcg_batch_with_executor(
        &graph,
        &preconditioner,
        &right_hand_sides,
        PcgOptions::default(),
        &too_small,
    )
    .unwrap_err();
    assert_eq!(
        error,
        CmgError::MemoryBudgetExceeded {
            required_bytes: workspace_bytes,
            budget_bytes: workspace_bytes - 1,
        }
    );
}

#[test]
fn one_thread_executor_preserves_serial_behavior() {
    let (graph, right_hand_sides) = path_problem(500, 3);
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let serial = solve_pcg_batch(
        &graph,
        &preconditioner,
        &right_hand_sides,
        PcgOptions::default(),
    )
    .unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let parallel = solve_pcg_batch_with_executor(
        &graph,
        &preconditioner,
        &right_hand_sides,
        PcgOptions::default(),
        &executor,
    )
    .unwrap();
    assert_eq!(serial, parallel);
}

#[test]
fn parallel_edge_sorting_matches_serial_canonicalization() {
    let raw_edges: Vec<(usize, usize, f64)> = (0usize..20_000)
        .flat_map(|index| {
            let left = index % 4_000;
            let right = (index.wrapping_mul(1_103).wrapping_add(17) % 4_000 + 1) % 4_000;
            let right = if right == left {
                (right + 1) % 4_000
            } else {
                right
            };
            let weight = 0.25 + (index % 29) as f64 / 7.0;
            [(left, right, weight), (right, left, weight / 3.0)]
        })
        .rev()
        .collect();
    let serial = Laplacian::from_edges(4_000, raw_edges.iter().copied()).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let parallel = Laplacian::from_edges_with_executor(4_000, raw_edges, &executor).unwrap();

    assert_eq!(serial, parallel);
}

#[test]
fn parallel_heavy_edge_selection_and_grouping_match_serial_exactly() {
    let raw_edges: Vec<(usize, usize, f64)> = (0usize..80_000)
        .flat_map(|index| {
            let left = index % 12_000;
            let right = (index.wrapping_mul(48_271).wrapping_add(7) % 12_000 + 1) % 12_000;
            let right = if right == left {
                (right + 1) % 12_000
            } else {
                right
            };
            let weight = 1.0 + (index % 11) as f64;
            [(left, right, weight), (right, left, weight)]
        })
        .collect();
    let graph = Laplacian::from_edges(12_000, raw_edges).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();

    let serial_forest = maximum_weight_forest(&graph);
    let parallel_forest = maximum_weight_forest_with_executor(&graph, &executor).unwrap();
    assert_eq!(serial_forest, parallel_forest);

    let serial_grouping = build_forest_grouping(&graph, 0.125).unwrap();
    let parallel_grouping = build_forest_grouping_with_executor(&graph, 0.125, &executor).unwrap();
    assert_eq!(serial_grouping, parallel_grouping);
}

#[test]
fn parallel_contraction_and_hierarchy_match_serial_exactly() {
    let (graph, _) = path_problem(20_000, 1);
    let labels: Vec<usize> = (0..graph.vertex_count()).map(|vertex| vertex / 3).collect();
    let aggregation = Aggregation::new(labels, graph.vertex_count().div_ceil(3)).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();

    let serial_coarse = aggregation.contract(&graph).unwrap();
    let parallel_coarse = aggregation
        .contract_with_executor(&graph, &executor)
        .unwrap();
    assert_eq!(serial_coarse, parallel_coarse);

    let options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let serial_hierarchy = CmgHierarchy::build(&graph, options).unwrap();
    let parallel_hierarchy = CmgHierarchy::build_with_executor(&graph, options, &executor).unwrap();
    assert_eq!(serial_hierarchy, parallel_hierarchy);

    let serial_preconditioner = CmgPreconditioner::build(&graph, options).unwrap();
    let parallel_preconditioner =
        CmgPreconditioner::build_with_executor(&graph, options, &executor).unwrap();
    assert_eq!(serial_preconditioner, parallel_preconditioner);
}

#[test]
fn parallel_cmg_plan_matches_stationary_cycle_and_rejects_other_hierarchies() {
    let (graph, rhs) = worker_firm_problem(10_000);
    let options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert!(plan.operator_count() > 0);
    assert!(plan.byte_len() > 0);

    let mut serial_output = vec![0.0; graph.vertex_count()];
    let mut serial_workspace = preconditioner.workspace();
    preconditioner
        .apply_compatible_into(&rhs, &mut serial_output, &mut serial_workspace)
        .unwrap();

    let mut parallel_output = vec![0.0; graph.vertex_count()];
    let mut parallel_workspace = preconditioner.workspace();
    plan.apply_compatible_into(
        &preconditioner,
        &rhs,
        &mut parallel_output,
        &mut parallel_workspace,
        &executor,
    )
    .unwrap();

    for (serial, parallel) in serial_output.iter().zip(&parallel_output) {
        let scale = 1.0_f64.max(serial.abs()).max(parallel.abs());
        assert!((serial - parallel).abs() <= 2.0e-11 * scale);
    }

    let rebuilt = Laplacian::from_edges(
        graph.vertex_count(),
        graph
            .edges()
            .iter()
            .map(|edge| (edge.u(), edge.v(), edge.weight())),
    )
    .unwrap();
    let other_preconditioner = CmgPreconditioner::build(&rebuilt, options).unwrap();
    let error = plan
        .apply_compatible_into(
            &other_preconditioner,
            &rhs,
            &mut parallel_output,
            &mut parallel_workspace,
            &executor,
        )
        .unwrap_err();
    assert_eq!(
        error,
        CmgError::InvalidHierarchy {
            context: "parallel CMG plan belongs to a different hierarchy",
        }
    );
}

#[test]
fn one_thread_parallel_cmg_plan_is_bitwise_serial() {
    let (graph, right_hand_sides) = path_problem(4_000, 1);
    let options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert_eq!(plan.operator_count(), 0);
    assert_eq!(plan.byte_len(), 0);

    let rhs = &right_hand_sides[0];
    let mut serial_output = vec![0.0; graph.vertex_count()];
    let mut serial_workspace = preconditioner.workspace();
    preconditioner
        .apply_compatible_into(rhs, &mut serial_output, &mut serial_workspace)
        .unwrap();

    let mut parallel_output = vec![0.0; graph.vertex_count()];
    let mut parallel_workspace = preconditioner.workspace();
    plan.apply_compatible_into(
        &preconditioner,
        rhs,
        &mut parallel_output,
        &mut parallel_workspace,
        &executor,
    )
    .unwrap();

    assert_eq!(serial_output, parallel_output);
}

#[test]
fn planned_parallel_pcg_matches_serial_certification() {
    let (graph, rhs) = worker_firm_problem(20_000);
    let cmg_options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, cmg_options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 4,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert!(plan.operator_count() > 0);

    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let serial = solve_pcg_with_workspace(
        &graph,
        &preconditioner,
        &rhs,
        PcgOptions::default(),
        &mut serial_workspace,
    )
    .unwrap();
    let mut parallel_workspace = PcgWorkspace::new(&preconditioner);
    let parallel = solve_pcg_with_plan_and_workspace(
        &graph,
        &preconditioner,
        &plan,
        &rhs,
        PcgOptions::default(),
        &mut parallel_workspace,
        &executor,
    )
    .unwrap();

    assert_eq!(serial.iterations(), parallel.iterations());
    assert_eq!(serial.restarts(), parallel.restarts());
    assert!(parallel.backward_error() <= parallel.tolerance());
    for (serial_value, parallel_value) in serial.solution().iter().zip(parallel.solution()) {
        let scale = 1.0_f64.max(serial_value.abs()).max(parallel_value.abs());
        assert!((serial_value - parallel_value).abs() <= 5.0e-10 * scale);
    }
}

#[test]
fn one_thread_planned_pcg_is_bitwise_serial() {
    let (graph, right_hand_sides) = path_problem(4_000, 1);
    let cmg_options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let preconditioner = CmgPreconditioner::build(&graph, cmg_options).unwrap();
    let executor = ParallelExecutor::new(ParallelOptions {
        threads: 1,
        min_parallel_len: 1,
        ..ParallelOptions::default()
    })
    .unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, &executor).unwrap();
    assert_eq!(plan.operator_count(), 0);

    let rhs = &right_hand_sides[0];
    let mut serial_workspace = PcgWorkspace::new(&preconditioner);
    let serial = solve_pcg_with_workspace(
        &graph,
        &preconditioner,
        rhs,
        PcgOptions::default(),
        &mut serial_workspace,
    )
    .unwrap();
    let mut planned_workspace = PcgWorkspace::new(&preconditioner);
    let planned = solve_pcg_with_plan_and_workspace(
        &graph,
        &preconditioner,
        &plan,
        rhs,
        PcgOptions::default(),
        &mut planned_workspace,
        &executor,
    )
    .unwrap();

    assert_eq!(serial, planned);
}
