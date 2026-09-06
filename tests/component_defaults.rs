//! Default builder coverage independent of experimental feature selection.
use cmg::{
    CmgOptions, CmgPreconditioner, Components, Laplacian, PcgOptions, PcgWorkspace,
    solve_pcg_into_with_workspace,
};

fn graph(path: usize, pairs: usize, isolates: usize) -> Laplacian {
    let n = path + 2 * pairs + isolates;
    let mut permutation: Vec<_> = (0..n).collect();
    for i in (1..n).rev() {
        permutation.swap(i, (i * 37 + 13) % (i + 1));
    }
    let edges = (0..path.saturating_sub(1))
        .map(|v| (v, v + 1, 0.5 + (v % 7) as f64))
        .chain((0..pairs).map(|p| (path + 2 * p, path + 2 * p + 1, 0.25 + (p % 11) as f64)))
        .map(|(u, v, w)| (permutation[u], permutation[v], w));
    Laplacian::from_edges(n, edges).unwrap()
}

#[test]
fn ordinary_defaults_cover_empty_terminal_and_interleaved_compact_graphs() {
    for g in [
        graph(0, 0, 0),
        graph(0, 0, 3),
        graph(0, 349, 0),
        graph(0, 350, 0),
        graph(128, 40, 3),
    ] {
        let pre = CmgPreconditioner::build(&g, CmgOptions::default()).unwrap();
        let mut ws = PcgWorkspace::new(&pre);
        let mut target: Vec<_> = (0..g.vertex_count()).map(|v| (v % 13) as f64).collect();
        Components::from_laplacian(&g)
            .center_in_place(&mut target)
            .unwrap();
        let rhs = g.matvec(&target).unwrap();
        let mut out = vec![42.0; target.len()];
        for guess in [None, Some(target.as_slice()), None] {
            let d = solve_pcg_into_with_workspace(
                &g,
                &pre,
                &rhs,
                guess,
                &mut out,
                PcgOptions::default(),
                &mut ws,
            )
            .unwrap();
            let product = g.matvec(&out).unwrap();
            let residual = rhs
                .iter()
                .zip(product)
                .map(|(b, ax)| (b - ax).powi(2))
                .sum::<f64>()
                .sqrt();
            assert!(residual <= d.tolerance());
            for (x, y) in out.iter().zip(&target) {
                assert!((x - y).abs() <= 1e-5);
            }
        }
        if !rhs.is_empty() {
            let saved = out.clone();
            let mut invalid = rhs.clone();
            invalid[0] = f64::NAN;
            assert!(
                solve_pcg_into_with_workspace(
                    &g,
                    &pre,
                    &invalid,
                    None,
                    &mut out,
                    PcgOptions::default(),
                    &mut ws
                )
                .is_err()
            );
            assert_eq!(out, saved);
        }
    }
}

#[cfg(feature = "experimental-components")]
#[test]
fn ordinary_defaults_match_explicit_preserved_stopping_policy() {
    use cmg::ComponentBuildOptions;
    for g in [
        graph(0, 0, 0),
        graph(0, 350, 0),
        graph(128, 0, 0),
        graph(128, 40, 3),
        graph(4096, 1000, 0),
    ] {
        for threshold in [2, 8, 700] {
            let options = CmgOptions {
                direct_threshold: threshold,
                ..CmgOptions::default()
            };
            let ordinary = CmgPreconditioner::build(&g, options).unwrap();
            let explicit = CmgPreconditioner::build_component_experiment(
                &g,
                options,
                ComponentBuildOptions {
                    prune_coarse_isolates: true,
                    preserve_unpruned_stopping: true,
                    factor_terminal_components: true,
                },
            )
            .unwrap();
            assert_eq!(ordinary, explicit);
        }
    }
}

#[cfg(feature = "parallel")]
#[test]
fn executor_and_prepared_builders_share_compact_policy() {
    use cmg::{ParallelExecutor, ParallelOptions, ParallelPcgSolver};
    let g = graph(4096, 1000, 3);
    let scalar = CmgPreconditioner::build(&g, CmgOptions::default()).unwrap();
    assert!(
        scalar
            .hierarchy()
            .levels()
            .iter()
            .any(|l| l.pruned_transfer().is_some())
    );
    for threads in [1, 4] {
        let options = ParallelOptions {
            threads,
            min_parallel_len: 1,
            ..ParallelOptions::default()
        };
        let executor = ParallelExecutor::new(options).unwrap();
        let parallel =
            CmgPreconditioner::build_with_executor(&g, CmgOptions::default(), &executor).unwrap();
        assert_eq!(parallel, scalar);
        let prepared = ParallelPcgSolver::build(&g, CmgOptions::default(), options).unwrap();
        assert_eq!(prepared.preconditioner(), &scalar);
        #[cfg(feature = "profiling")]
        {
            let (recorded, _) = CmgPreconditioner::build_with_executor_profiled(
                &g,
                CmgOptions::default(),
                &executor,
            )
            .unwrap();
            assert_eq!(recorded, scalar);
        }
    }
}
