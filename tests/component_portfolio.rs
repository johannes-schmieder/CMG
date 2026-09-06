#![cfg(feature = "experimental-components")]

use cmg::{
    CmgError, CmgOptions, ComponentPcgExperiment, Components, GroundedLdl, Laplacian, PcgOptions,
    ValidationOptions,
};

fn mixed(n: usize, pairs: usize, isolates: usize, interleaved: bool) -> Laplacian {
    let total = n + 2 * pairs + isolates;
    let map = |v: usize| {
        if interleaved {
            (v / 2) + if v % 2 == 0 { 0 } else { total.div_ceil(2) }
        } else {
            v
        }
    };
    Laplacian::from_edges(
        total,
        (0..n.saturating_sub(1))
            .map(|v| (map(v), map(v + 1), 0.5 + (v % 7) as f64))
            .chain(
                (0..pairs).map(|p| (map(n + 2 * p), map(n + 2 * p + 1), 0.125 + (p % 11) as f64)),
            ),
    )
    .unwrap()
}

fn target(graph: &Laplacian, offset: usize) -> Vec<f64> {
    let mut x: Vec<_> = (0..graph.vertex_count())
        .map(|v| ((v * 13 + offset) % 31) as f64 - 15.0)
        .collect();
    Components::from_laplacian(graph)
        .center_in_place(&mut x)
        .unwrap();
    x
}

fn norm(x: &[f64]) -> f64 {
    x.iter().map(|x| x * x).sum::<f64>().sqrt()
}

#[test]
fn empty_tiny_connected_and_mixed_solves_match_independent_grounded_reference() {
    for (n, pairs, isolates) in [(0, 0, 0), (0, 0, 5), (0, 17, 3), (27, 0, 0), (27, 13, 5)] {
        for interleaved in [false, true] {
            let graph = mixed(n, pairs, isolates, interleaved);
            let experiment = ComponentPcgExperiment::build(
                &graph,
                CmgOptions {
                    direct_threshold: 4,
                    ..CmgOptions::default()
                },
            )
            .unwrap();
            assert_eq!(experiment.active_vertices(), n);
            assert_eq!(experiment.direct_vertices(), pairs * 2 + isolates);
            let mut ws = experiment.workspace().unwrap();
            let ws_bytes = ws.byte_len();
            let original = GroundedLdl::factor(&graph).unwrap();
            let mut out = vec![99.0; graph.vertex_count()];
            for offset in [0, 7, 0] {
                let x = target(&graph, offset);
                let rhs = graph.matvec(&x).unwrap();
                let mut direct = original.solve(&rhs).unwrap();
                Components::from_laplacian(&graph)
                    .center_in_place(&mut direct)
                    .unwrap();
                let guess: Vec<_> = x.iter().map(|x| x * 0.3 + 7.0).collect();
                for initial in [None, Some(guess.as_slice())] {
                    let report = experiment
                        .solve_into(
                            &rhs,
                            initial,
                            &mut out,
                            PcgOptions {
                                relative_tolerance: 1e-12,
                                residual_recompute_interval: 3,
                                ..PcgOptions::default()
                            },
                            &mut ws,
                        )
                        .unwrap();
                    let residual: Vec<_> = graph
                        .matvec(&out)
                        .unwrap()
                        .iter()
                        .zip(&rhs)
                        .map(|(a, b)| b - a)
                        .collect();
                    assert!(norm(&residual) <= report.original().tolerance());
                    assert!(
                        norm(
                            &out.iter()
                                .zip(&direct)
                                .map(|(x, y)| x - y)
                                .collect::<Vec<_>>()
                        ) <= 1e-7 * (1.0 + norm(&direct))
                    );
                    assert!(report.original().backward_error() <= 1e-12);
                    for sum in Components::from_laplacian(&graph).sums(&out).unwrap() {
                        assert!(sum.abs() <= 1e-10 * (1.0 + norm(&out)));
                    }
                    assert_eq!(ws.byte_len(), ws_bytes);
                }
            }
            assert_eq!(experiment.graph(), &graph);
        }
    }
}

#[test]
fn a_complete_initial_guess_is_certified_before_any_subsystem_solve() {
    let graph = mixed(37, 11, 3, true);
    let experiment = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    let mut ws = experiment.workspace().unwrap();
    let x = target(&graph, 5);
    let rhs = graph.matvec(&x).unwrap();
    let mut out = vec![0.0; x.len()];
    let report = experiment
        .solve_into(&rhs, Some(&x), &mut out, PcgOptions::default(), &mut ws)
        .unwrap();
    assert_eq!(report.original().iterations(), 0);
    assert!(report.active().is_none());
    assert!(report.original().initial_residual_norm() <= report.original().tolerance());
}

#[test]
fn invalid_full_inputs_and_foreign_workspaces_never_change_output() {
    let graph = mixed(7, 3, 2, true);
    let experiment = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    let mut ws = experiment.workspace().unwrap();
    let rhs = graph.matvec(&target(&graph, 0)).unwrap();
    let n = rhs.len();
    let mut out = vec![99.0; n];
    assert!(
        experiment
            .solve_into(
                &rhs[..n - 1],
                None,
                &mut out,
                PcgOptions::default(),
                &mut ws
            )
            .is_err()
    );
    assert!(
        experiment
            .solve_into(
                &rhs,
                Some(&rhs[..n - 1]),
                &mut out,
                PcgOptions::default(),
                &mut ws
            )
            .is_err()
    );
    assert!(
        experiment
            .solve_into(
                &rhs,
                None,
                &mut out[..n - 1],
                PcgOptions::default(),
                &mut ws
            )
            .is_err()
    );
    for vertex in 0..n {
        let mut invalid = rhs.clone();
        invalid[vertex] = f64::NAN;
        assert!(
            experiment
                .solve_into(&invalid, None, &mut out, PcgOptions::default(), &mut ws)
                .is_err()
        );
        assert!(
            experiment
                .solve_into(
                    &rhs,
                    Some(&invalid),
                    &mut out,
                    PcgOptions::default(),
                    &mut ws
                )
                .is_err()
        );
    }
    let mut incompatible = rhs.clone();
    incompatible[graph.diagonal().iter().position(|&d| d == 0.0).unwrap()] = 1.0;
    assert!(matches!(
        experiment.solve_into(
            &incompatible,
            None,
            &mut out,
            PcgOptions::default(),
            &mut ws
        ),
        Err(CmgError::IncompatibleLaplacianRhs { .. })
    ));
    let fresh = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    assert!(matches!(
        fresh.solve_into(&rhs, None, &mut out, PcgOptions::default(), &mut ws),
        Err(CmgError::InvalidHierarchy { .. })
    ));
    assert_eq!(out, vec![99.0; n]);
    experiment
        .clone()
        .solve_into(&rhs, None, &mut out, PcgOptions::default(), &mut ws)
        .unwrap();
    assert_ne!(out, vec![99.0; n]);
}

#[test]
fn original_projection_defect_can_fail_final_certificate_without_fallback() {
    let graph = Laplacian::from_edges(2, [(0, 1, 1.0)]).unwrap();
    let experiment = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    let mut ws = experiment.workspace().unwrap();
    let mut out = [99.0; 2];
    let rhs = [1.0, -1.0 + 1e-8];
    let options = PcgOptions {
        relative_tolerance: 1e-16,
        validation: ValidationOptions {
            compatibility_tolerance: 1e-6,
            ..ValidationOptions::default()
        },
        ..PcgOptions::default()
    };
    assert!(matches!(
        experiment.solve_into(&rhs, None, &mut out, options, &mut ws),
        Err(CmgError::ResidualVerificationFailed { .. })
    ));
    assert_eq!(out, [99.0; 2]);
    // A separately requested absolute tolerance is honored without changing the preparation.
    let report = experiment
        .solve_into(
            &rhs,
            None,
            &mut out,
            PcgOptions {
                absolute_tolerance: 1e-7,
                ..options
            },
            &mut ws,
        )
        .unwrap();
    assert!(report.original().rhs_projection_norm() > 0.0);
    assert!(report.original().residual_norm() > 0.0);
    assert!(report.active().is_none());
}

#[test]
fn active_iteration_failure_is_propagated_and_workspace_can_be_reused() {
    let graph = mixed(129, 8, 3, false);
    let experiment = ComponentPcgExperiment::build(
        &graph,
        CmgOptions {
            direct_threshold: 4,
            ..CmgOptions::default()
        },
    )
    .unwrap();
    let mut ws = experiment.workspace().unwrap();
    let rhs = graph.matvec(&target(&graph, 2)).unwrap();
    let mut out = vec![99.0; rhs.len()];
    assert!(matches!(
        experiment.solve_into(
            &rhs,
            None,
            &mut out,
            PcgOptions {
                relative_tolerance: 1e-14,
                max_iterations: 1,
                ..PcgOptions::default()
            },
            &mut ws
        ),
        Err(CmgError::MaximumIterations { .. }) | Err(CmgError::ResidualVerificationFailed { .. })
    ));
    assert_eq!(out, vec![99.0; rhs.len()]);
    experiment
        .solve_into(&rhs, None, &mut out, PcgOptions::default(), &mut ws)
        .unwrap();
}

#[test]
fn only_isolates_and_pairs_are_removed_and_weight_changes_need_new_preparation() {
    let graph =
        Laplacian::from_edges(6, [(0, 1, 1.0), (1, 2, 2.0), (0, 2, 3.0), (3, 4, 1e-100)]).unwrap();
    let experiment = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    assert_eq!(experiment.active_vertices(), 3);
    assert_eq!(experiment.direct_vertices(), 3);
    let changed =
        Laplacian::from_edges(6, [(0, 1, 2.0), (1, 2, 4.0), (0, 2, 6.0), (3, 4, 1e100)]).unwrap();
    let next = ComponentPcgExperiment::build(&changed, CmgOptions::default()).unwrap();
    let mut old_ws = experiment.workspace().unwrap();
    let rhs = changed.matvec(&target(&changed, 0)).unwrap();
    let mut out = [99.0; 6];
    assert!(
        next.solve_into(&rhs, None, &mut out, PcgOptions::default(), &mut old_ws)
            .is_err()
    );
    next.solve_into(
        &rhs,
        None,
        &mut out,
        PcgOptions::default(),
        &mut next.workspace().unwrap(),
    )
    .unwrap();
}

#[test]
fn invalid_options_and_unrepresentable_direct_solution_return_errors() {
    let graph = Laplacian::from_edges(2, [(0, 1, f64::MIN_POSITIVE / 1024.0)]).unwrap();
    let experiment = ComponentPcgExperiment::build(&graph, CmgOptions::default()).unwrap();
    let mut ws = experiment.workspace().unwrap();
    let mut out = [99.0; 2];
    for options in [
        PcgOptions {
            max_iterations: 0,
            ..PcgOptions::default()
        },
        PcgOptions {
            residual_recompute_interval: 0,
            ..PcgOptions::default()
        },
        PcgOptions {
            relative_tolerance: f64::NAN,
            ..PcgOptions::default()
        },
        PcgOptions {
            relative_tolerance: 0.0,
            absolute_tolerance: 0.0,
            ..PcgOptions::default()
        },
    ] {
        assert!(matches!(
            experiment.solve_into(&[1.0, -1.0], None, &mut out, options, &mut ws),
            Err(CmgError::InvalidOption { .. })
        ));
    }
    assert!(
        experiment
            .solve_into(&[1.0, -1.0], None, &mut out, PcgOptions::default(), &mut ws)
            .is_err()
    );
    assert_eq!(out, [99.0; 2]);
}
