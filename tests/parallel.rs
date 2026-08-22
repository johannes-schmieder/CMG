#![cfg(feature = "parallel")]

use cmg::{
    Aggregation, CmgError, CmgHierarchy, CmgOptions, CmgPreconditioner, Laplacian,
    ParallelExecutor, ParallelOptions,
    PcgOptions, PcgWorkspace, solve_pcg_batch, solve_pcg_batch_with_executor,
};

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
    let raw_edges: Vec<(usize, usize, f64)> = (0..20_000)
        .flat_map(|index| {
            let left = index % 4_000;
            let right = (index.wrapping_mul(1_103).wrapping_add(17) % 4_000 + 1) % 4_000;
            let right = if right == left { (right + 1) % 4_000 } else { right };
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
    let parallel =
        Laplacian::from_edges_with_executor(4_000, raw_edges, &executor).unwrap();

    assert_eq!(serial, parallel);
}

#[test]
fn parallel_contraction_and_hierarchy_match_serial_exactly() {
    let (graph, _) = path_problem(20_000, 1);
    let labels: Vec<usize> = (0..graph.vertex_count()).map(|vertex| vertex / 3).collect();
    let aggregation = Aggregation::new(
        labels,
        graph.vertex_count().div_ceil(3),
    )
    .unwrap();
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
    let parallel_hierarchy =
        CmgHierarchy::build_with_executor(&graph, options, &executor).unwrap();
    assert_eq!(serial_hierarchy, parallel_hierarchy);

    let serial_preconditioner = CmgPreconditioner::build(&graph, options).unwrap();
    let parallel_preconditioner =
        CmgPreconditioner::build_with_executor(&graph, options, &executor).unwrap();
    assert_eq!(serial_preconditioner, parallel_preconditioner);
}
