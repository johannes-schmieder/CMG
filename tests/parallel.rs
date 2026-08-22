#![cfg(feature = "parallel")]

use cmg::{
    CmgError, CmgOptions, CmgPreconditioner, Laplacian, ParallelExecutor, ParallelOptions,
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
