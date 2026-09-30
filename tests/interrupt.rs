use cmg::{
    CmgError, CmgOptions, CmgPreconditioner, Laplacian, PcgOptions, PcgWorkspace,
    solve_pcg_into_with_workspace, solve_pcg_into_with_workspace_and_poll,
};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicUsize, Ordering};

#[test]
fn every_scalar_poll_aborts_without_publishing_and_workspace_retries_match() {
    let graph = Laplacian::from_edges(130, (0..129).map(|v| (v, v + 1, 0.5 + (v % 7) as f64 / 4.)))
        .unwrap();
    let pre = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: 2,
            ..CmgOptions::default()
        },
    )
    .unwrap();
    let target: Vec<_> = (0..130).map(|i| ((i * 13) % 31) as f64 - 15.).collect();
    let rhs = graph.matvec(&target).unwrap();
    let guess: Vec<_> = target.iter().map(|v| v / 3.).collect();
    let options = PcgOptions {
        relative_tolerance: 1e-10,
        residual_recompute_interval: 3,
        ..PcgOptions::default()
    };
    for initial in [None, Some(guess.as_slice())] {
        let mut reference = vec![0.; 130];
        let mut workspace = PcgWorkspace::new(&pre);
        let original = solve_pcg_into_with_workspace(
            &graph,
            &pre,
            &rhs,
            initial,
            &mut reference,
            options,
            &mut workspace,
        )
        .unwrap();
        let calls = AtomicUsize::new(0);
        let mut traced = vec![0.; 130];
        let traced_diagnostics = solve_pcg_into_with_workspace_and_poll(
            &graph,
            &pre,
            &rhs,
            initial,
            &mut traced,
            options,
            &mut workspace,
            Some(&|| {
                calls.fetch_add(1, Ordering::SeqCst);
                true
            }),
        )
        .unwrap();
        assert_eq!(original, traced_diagnostics);
        assert!(
            reference
                .iter()
                .zip(&traced)
                .all(|(a, b)| a.to_bits() == b.to_bits())
        );
        assert_eq!(calls.load(Ordering::SeqCst), original.iterations() + 1);
        for stop in 0..calls.load(Ordering::SeqCst) {
            let count = AtomicUsize::new(0);
            let mut output = vec![1234.; 130];
            assert_eq!(
                solve_pcg_into_with_workspace_and_poll(
                    &graph,
                    &pre,
                    &rhs,
                    initial,
                    &mut output,
                    options,
                    &mut workspace,
                    Some(&|| count.fetch_add(1, Ordering::SeqCst) != stop)
                ),
                Err(CmgError::Interrupted)
            );
            assert_eq!(output, vec![1234.; 130]);
            let retried = solve_pcg_into_with_workspace(
                &graph,
                &pre,
                &rhs,
                initial,
                &mut output,
                options,
                &mut workspace,
            )
            .unwrap();
            assert_eq!(original, retried);
            assert!(
                reference
                    .iter()
                    .zip(&output)
                    .all(|(a, b)| a.to_bits() == b.to_bits())
            );
        }
        let mut output = vec![1234.; 130];
        assert!(
            catch_unwind(AssertUnwindSafe(|| solve_pcg_into_with_workspace_and_poll(
                &graph,
                &pre,
                &rhs,
                initial,
                &mut output,
                options,
                &mut workspace,
                Some(&|| panic!("poll panic"))
            )))
            .is_err()
        );
        assert_eq!(output, vec![1234.; 130]);
        assert_eq!(
            solve_pcg_into_with_workspace(
                &graph,
                &pre,
                &rhs,
                initial,
                &mut output,
                options,
                &mut workspace
            )
            .unwrap(),
            original
        );
    }
}
