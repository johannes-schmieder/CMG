#![cfg(feature = "experimental-fused-rhs")]

use cmg::experimental::{FusedPcgWorkspace4, solve_pcg_batch_fused_width4_into_with_workspace};
use cmg::{
    CmgOptions, CmgPreconditioner, Laplacian, PcgBatchMut, PcgBatchRef, PcgBatchWorkspace,
    PcgDiagnostics, PcgOptions, solve_pcg_batch_into_with_workspace,
};

fn path(vertex_count: usize) -> Laplacian {
    Laplacian::from_edges(
        vertex_count,
        (0..vertex_count.saturating_sub(1))
            .map(|vertex| (vertex, vertex + 1, 0.5 + (vertex % 7) as f64 / 8.0)),
    )
    .unwrap()
}

fn rhs_batch(graph: &Laplacian, count: usize) -> Vec<f64> {
    let dimension = graph.vertex_count();
    let mut packed = Vec::with_capacity(count * dimension);
    for rhs_index in 0..count {
        let target: Vec<f64> = if rhs_index == 0 {
            vec![0.0; dimension]
        } else {
            (0..dimension)
                .map(|vertex| {
                    let x = vertex as f64 + 0.375 * rhs_index as f64;
                    x.sin() + 0.125 * (3.0 * x).cos() + 0.001 * x * x
                })
                .collect()
        };
        packed.extend(graph.matvec(&target).unwrap());
    }
    packed
}

fn compare_paths(graph: &Laplacian, cmg_options: CmgOptions, rhs_count: usize) {
    let preconditioner = CmgPreconditioner::build(graph, cmg_options).unwrap();
    let rhs = rhs_batch(graph, rhs_count);
    let mut scalar_output = vec![f64::NAN; rhs.len()];
    let mut fused_output = vec![f64::NAN; rhs.len()];
    let mut scalar_diagnostics = vec![PcgDiagnostics::default(); rhs_count];
    let mut fused_diagnostics = vec![PcgDiagnostics::default(); rhs_count];
    let options = PcgOptions {
        relative_tolerance: 1.0e-10,
        max_iterations: 600,
        residual_recompute_interval: 7,
        ..PcgOptions::default()
    };
    let mut scalar_workspace = PcgBatchWorkspace::new(&preconditioner).unwrap();
    solve_pcg_batch_into_with_workspace(
        graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, rhs_count, graph.vertex_count()).unwrap(),
        None,
        PcgBatchMut::contiguous(&mut scalar_output, rhs_count, graph.vertex_count()).unwrap(),
        &mut scalar_diagnostics,
        options,
        &mut scalar_workspace,
    )
    .unwrap();
    let mut fused_workspace = FusedPcgWorkspace4::new(&preconditioner);
    let bytes = fused_workspace.byte_len();
    solve_pcg_batch_fused_width4_into_with_workspace(
        graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, rhs_count, graph.vertex_count()).unwrap(),
        PcgBatchMut::contiguous(&mut fused_output, rhs_count, graph.vertex_count()).unwrap(),
        &mut fused_diagnostics,
        options,
        &mut fused_workspace,
    )
    .unwrap();
    assert_eq!(fused_workspace.byte_len(), bytes);
    assert_eq!(scalar_diagnostics, fused_diagnostics);
    for (scalar, fused) in scalar_output.iter().zip(&fused_output) {
        assert_eq!(scalar.to_bits(), fused.to_bits());
    }
}

#[test]
fn direct_and_multilevel_batches_are_bitwise_scalar() {
    for rhs_count in [1, 3, 4, 5, 7, 8, 9] {
        compare_paths(&path(24), CmgOptions::default(), rhs_count);
        compare_paths(
            &path(96),
            CmgOptions {
                direct_threshold: 2,
                ..CmgOptions::default()
            },
            rhs_count,
        );
    }
}

#[test]
fn disconnected_weighted_and_strided_batch_is_bitwise_scalar() {
    let graph = Laplacian::from_edges(
        9,
        [
            (0, 1, 1.0e-3),
            (1, 2, 7.0),
            (0, 2, 0.125),
            (3, 4, 2.0),
            (4, 5, 9.0e2),
            (6, 7, 0.75),
        ],
    )
    .unwrap();
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let rhs_count = 5;
    let contiguous = rhs_batch(&graph, rhs_count);
    let mut rhs = vec![91.0; graph.vertex_count() * 7];
    for vertex in 0..graph.vertex_count() {
        for item in 0..rhs_count {
            rhs[vertex * 7 + item] = contiguous[item * graph.vertex_count() + vertex];
        }
    }
    let options = PcgOptions {
        relative_tolerance: 1.0e-11,
        residual_recompute_interval: 3,
        ..PcgOptions::default()
    };
    let mut scalar = vec![-77.0; graph.vertex_count() * 8];
    let mut fused = scalar.clone();
    let mut scalar_diagnostics = vec![PcgDiagnostics::default(); rhs_count];
    let mut fused_diagnostics = scalar_diagnostics.clone();
    solve_pcg_batch_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::strided(&rhs, rhs_count, graph.vertex_count(), 1, 7).unwrap(),
        None,
        PcgBatchMut::strided(&mut scalar, rhs_count, graph.vertex_count(), 1, 8).unwrap(),
        &mut scalar_diagnostics,
        options,
        &mut PcgBatchWorkspace::new(&preconditioner).unwrap(),
    )
    .unwrap();
    solve_pcg_batch_fused_width4_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::strided(&rhs, rhs_count, graph.vertex_count(), 1, 7).unwrap(),
        PcgBatchMut::strided(&mut fused, rhs_count, graph.vertex_count(), 1, 8).unwrap(),
        &mut fused_diagnostics,
        options,
        &mut FusedPcgWorkspace4::new(&preconditioner),
    )
    .unwrap();
    assert_eq!(scalar_diagnostics, fused_diagnostics);
    assert_eq!(scalar.len(), fused.len());
    for (scalar, fused) in scalar.iter().zip(&fused) {
        assert_eq!(scalar.to_bits(), fused.to_bits());
    }
}

#[test]
fn failure_preserves_scalar_prefix_observability() {
    let graph = path(16);
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let mut rhs = rhs_batch(&graph, 3);
    rhs[graph.vertex_count()] += 1.0;
    let mut scalar = vec![-13.0; rhs.len()];
    let mut fused = scalar.clone();
    let mut scalar_diagnostics = vec![PcgDiagnostics::default(); 3];
    let mut fused_diagnostics = scalar_diagnostics.clone();
    let scalar_error = solve_pcg_batch_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, 3, graph.vertex_count()).unwrap(),
        None,
        PcgBatchMut::contiguous(&mut scalar, 3, graph.vertex_count()).unwrap(),
        &mut scalar_diagnostics,
        PcgOptions::default(),
        &mut PcgBatchWorkspace::new(&preconditioner).unwrap(),
    )
    .unwrap_err();
    let fused_error = solve_pcg_batch_fused_width4_into_with_workspace(
        &graph,
        &preconditioner,
        PcgBatchRef::contiguous(&rhs, 3, graph.vertex_count()).unwrap(),
        PcgBatchMut::contiguous(&mut fused, 3, graph.vertex_count()).unwrap(),
        &mut fused_diagnostics,
        PcgOptions::default(),
        &mut FusedPcgWorkspace4::new(&preconditioner),
    )
    .unwrap_err();
    assert_eq!(scalar_error, fused_error);
    assert_eq!(scalar_diagnostics, fused_diagnostics);
    for (scalar, fused) in scalar.iter().zip(&fused) {
        assert_eq!(scalar.to_bits(), fused.to_bits());
    }
}

#[test]
fn deterministic_randomized_graphs_are_bitwise_scalar() {
    let mut state = 0x9e37_79b9_7f4a_7c15_u64;
    for case in 0..16 {
        let vertex_count = 12 + case;
        let mut edges = Vec::new();
        for vertex in 0..vertex_count - 1 {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let weight = 0.125 + ((state >> 33) % 10_000) as f64 / 1_337.0;
            edges.push((vertex, vertex + 1, weight));
        }
        for left in 0..vertex_count {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let right = (state as usize % vertex_count)
                .max(left + 1)
                .min(vertex_count - 1);
            if left < right {
                let weight = 0.25 + ((state >> 29) % 1_000) as f64 / 211.0;
                edges.push((left, right, weight));
            }
        }
        let graph = Laplacian::from_edges(vertex_count, edges).unwrap();
        compare_paths(
            &graph,
            CmgOptions {
                direct_threshold: if case % 2 == 0 { 2 } else { 700 },
                ..CmgOptions::default()
            },
            1 + case % 9,
        );
    }
}
