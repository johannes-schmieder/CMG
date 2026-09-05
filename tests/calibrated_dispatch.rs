#![cfg(feature = "experimental-fused-rhs")]

use cmg::experimental::{
    BatchDispatchMode, BatchDispatchOptions, BatchDispatchRoute, CalibratedPcgBatchSolver,
};
use cmg::{CmgOptions, Laplacian, PcgBatchMut, PcgBatchRef, PcgDiagnostics, PcgOptions};
use std::time::Duration;

#[test]
fn documented_calibrated_example() -> Result<(), cmg::CmgError> {
    let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 1.0)])?;
    let mut solver = CalibratedPcgBatchSolver::build(
        &graph,
        CmgOptions::default(),
        BatchDispatchOptions::default(),
    )?;
    let rhs = [1.0, 0.0, -1.0].repeat(5);
    let mut output = vec![0.0; rhs.len()];
    let mut diagnostics = vec![PcgDiagnostics::default(); 5];
    let first = solver.solve_batch_into(
        PcgBatchRef::contiguous(&rhs, 5, 3)?,
        PcgBatchMut::contiguous(&mut output, 5, 3)?,
        &mut diagnostics,
        PcgOptions::default(),
    )?;
    assert_eq!(first.executed, BatchDispatchRoute::Scalar);
    let next = solver.solve_batch_into(
        PcgBatchRef::contiguous(&rhs, 5, 3)?,
        PcgBatchMut::contiguous(&mut output, 5, 3)?,
        &mut diagnostics,
        PcgOptions::default(),
    )?;
    assert!(next.cached);
    assert_eq!(next.executed, first.selected);
    solver.reset_calibration(); // New workload distribution or execution environment.
    solver.set_mode(BatchDispatchMode::Scalar); // Explicit downstream override.
    Ok(())
}

#[test]
fn input_failures_preserve_scalar_prefix_in_auto_and_forced_modes() {
    let graph = Laplacian::from_edges(3, [(0, 1, 1.0), (1, 2, 1.0)]).unwrap();
    for mode in [
        BatchDispatchMode::Auto,
        BatchDispatchMode::Scalar,
        BatchDispatchMode::Fused,
    ] {
        let mut solver = CalibratedPcgBatchSolver::build(
            &graph,
            CmgOptions::default(),
            BatchDispatchOptions {
                mode,
                calibration_budget: Duration::ZERO,
                ..BatchDispatchOptions::default()
            },
        )
        .unwrap();
        for invalid in [f64::NAN, f64::INFINITY] {
            let mut rhs = [1.0, 0.0, -1.0].repeat(5);
            rhs[6] = invalid;
            let mut output = vec![77.0; 15];
            let mut diagnostics = vec![PcgDiagnostics::default(); 5];
            assert!(
                solver
                    .solve_batch_into(
                        PcgBatchRef::contiguous(&rhs, 5, 3).unwrap(),
                        PcgBatchMut::contiguous(&mut output, 5, 3).unwrap(),
                        &mut diagnostics,
                        PcgOptions::default()
                    )
                    .is_err()
            );
            for chunk in output[..6].chunks(3) {
                assert!(
                    chunk
                        .iter()
                        .zip([1.0, 0.0, -1.0])
                        .all(|(a, b)| (a - b).abs() < 1e-12)
                );
            }
            assert_eq!(&output[6..], &[77.0; 9]);
            assert!(solver.calibration_report().is_none());
        }
        let rhs = [1.0, 0.0, -1.0].repeat(5);
        let mut output = vec![77.0; 15];
        let mut diagnostics = vec![PcgDiagnostics::default(); 5];
        let invalid_options = PcgOptions {
            relative_tolerance: f64::NAN,
            ..PcgOptions::default()
        };
        assert!(
            solver
                .solve_batch_into(
                    PcgBatchRef::contiguous(&rhs, 5, 3).unwrap(),
                    PcgBatchMut::contiguous(&mut output, 5, 3).unwrap(),
                    &mut diagnostics,
                    invalid_options
                )
                .is_err()
        );
        assert_eq!(output, vec![77.0; 15]);
    }
}
