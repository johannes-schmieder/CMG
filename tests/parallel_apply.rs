#![cfg(feature = "parallel")]
//! Team applications of the fixed cycle on a caller-owned Rayon pool.
//!
//! A plan and a borrowed team choose where rows run (row operators,
//! restriction rows, elementwise rows, prolongation, centering subtraction),
//! never the arithmetic: interleaved batches of every width and scalar
//! cycles without the fixed-chunk centering reproduce the team-free serial
//! bits at every worker ceiling; a scalar cycle whose single large coarse
//! component uses that centering reproduces the one-lane team reference.

use cmg::{
    ApplyContext, CmgBatchWorkspace, CmgOptions, CmgPreconditioner, Laplacian, ParallelCmgPlan,
    ParallelOptions, Team,
};

fn bits(values: &[f64]) -> Vec<u64> {
    values.iter().map(|value| value.to_bits()).collect()
}

/// Dense enough for row operators: workers with three skewed firms each.
fn worker_firm(workers: usize, firms: usize) -> Laplacian {
    let mut state = 11_u64;
    let mut below = |bound: usize| {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((state >> 11) % bound as u64) as usize
    };
    let mut edges = Vec::new();
    for worker in 0..workers {
        for _ in 0..3 {
            let firm = below(firms).min(below(firms));
            edges.push((worker, workers + firm, 1.0 + below(4) as f64 / 2.0));
        }
    }
    edges.extend((1..firms).map(|firm| (workers + firm - 1, workers + firm, 0.5)));
    Laplacian::from_edges(workers + firms, edges).unwrap()
}

/// Sparse (below the row-operator density floor): a weighted path with
/// sparse chords, `components` disjoint copies.
fn sparse(per_component: usize, components: usize) -> Laplacian {
    let edges = (0..components).flat_map(|c| {
        let base = c * per_component;
        (1..per_component)
            .map(move |v| (base + v - 1, base + v, 0.5 + (v % 7) as f64 / 4.0))
            .chain(
                (7..per_component)
                    .step_by(7)
                    .map(move |v| (base + v - 7, base + v, 0.25)),
            )
    });
    Laplacian::from_edges(per_component * components, edges).unwrap()
}

/// `columns` compatible right-hand sides, interleaved vertex-major.
fn interleaved(graph: &Laplacian, columns: usize) -> Vec<f64> {
    let n = graph.vertex_count();
    let lanes: Vec<Vec<f64>> = (0..columns)
        .map(|lane| {
            let known: Vec<f64> = (0..n)
                .map(|v| (((v * 29) + lane * 13) % 97) as f64 / 7.0 - 6.0)
                .collect();
            graph.matvec(&known).unwrap()
        })
        .collect();
    (0..n)
        .flat_map(|v| lanes.iter().map(move |rhs| rhs[v]))
        .collect()
}

fn pool(threads: usize) -> rayon::ThreadPool {
    rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .unwrap()
}

fn batch(
    preconditioner: &CmgPreconditioner,
    rhs: &[f64],
    columns: usize,
    context: ApplyContext<'_>,
) -> Vec<u64> {
    let mut workspace = CmgBatchWorkspace::try_new(preconditioner, 8, usize::MAX).unwrap();
    let mut output = vec![f64::NAN; rhs.len()];
    preconditioner
        .apply_compatible_batch_into(rhs, &mut output, columns, &mut workspace, context)
        .unwrap();
    bits(&output)
}

fn scalar(preconditioner: &CmgPreconditioner, rhs: &[f64], context: ApplyContext<'_>) -> Vec<u64> {
    let mut workspace = preconditioner.try_workspace().unwrap();
    let mut output = vec![f64::NAN; rhs.len()];
    preconditioner
        .apply_compatible_into_with(rhs, &mut output, &mut workspace, context)
        .unwrap();
    bits(&output)
}

/// Every width 1..=8 and the scalar cycle on teams of 1, 2, 4 and 8 lanes
/// borrowed from one caller pool: interleaved lanes equal the team-free
/// serial bits; scalar cycles equal the one-lane team reference (and the
/// team-free bits when no fixed-chunk centering applies). Repeated
/// applications on one workspace repeat.
fn check(graph: &Laplacian, options: ParallelOptions, expect_operators: bool) {
    let preconditioner = CmgPreconditioner::build(
        graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
    )
    .unwrap();
    assert!(preconditioner.hierarchy().levels().len() > 2);
    let host = pool(8);
    let rhs8 = interleaved(graph, 8);
    let rhs1: Vec<f64> = rhs8.iter().step_by(8).copied().collect();
    let serial: Vec<_> = (1..=8)
        .map(|columns| {
            let rhs: Vec<f64> = rhs8
                .chunks_exact(8)
                .flat_map(|row| row[..columns].to_vec())
                .collect();
            (
                rhs.clone(),
                batch(&preconditioner, &rhs, columns, ApplyContext::default()),
            )
        })
        .collect();
    let team_free = scalar(&preconditioner, &rhs1, ApplyContext::default());
    // Interleaved lanes keep the scalar team-free arithmetic.
    assert_eq!(serial[0].1, team_free);
    for (columns, (_, expected)) in serial.iter().enumerate().map(|(i, s)| (i + 1, s)) {
        for lane in 0..columns {
            let lane_bits: Vec<u64> = expected
                .iter()
                .skip(lane)
                .step_by(columns)
                .copied()
                .collect();
            assert_eq!(
                lane_bits,
                bits_of_lane(&serial[7].1, 8, lane),
                "width {columns}"
            );
        }
    }
    let one = Team::new(&host, 1, options).unwrap();
    let reference = scalar(
        &preconditioner,
        &rhs1,
        ApplyContext {
            plan: None,
            team: Some(one),
            cancel: None,
        },
    );
    for threads in [1, 2, 4, 8] {
        let team = Team::new(&host, threads, options).unwrap();
        let plan = ParallelCmgPlan::build(&preconditioner, team).unwrap();
        if threads > 1 {
            assert!(plan.restriction_count() > 0, "{threads} lanes");
            if expect_operators {
                assert!(plan.operator_count() > 0, "{threads} lanes");
            } else {
                // The sparse finest level is operator-free: its rows restrict
                // on the team without a row operator.
                assert!(
                    plan.restriction_count() > plan.operator_count(),
                    "{threads} lanes"
                );
            }
        } else {
            assert_eq!((plan.operator_count(), plan.restriction_count()), (0, 0));
        }
        let context = ApplyContext {
            plan: Some(&plan),
            team: Some(team),
            cancel: None,
        };
        for (columns, (rhs, expected)) in serial.iter().enumerate().map(|(i, s)| (i + 1, s)) {
            assert_eq!(
                &batch(&preconditioner, rhs, columns, context),
                expected,
                "{threads} lanes, width {columns}"
            );
        }
        let planned = scalar(&preconditioner, &rhs1, context);
        assert_eq!(planned, reference, "{threads} lanes, scalar");
        // One workspace, repeated: the same bits.
        let mut workspace = CmgBatchWorkspace::try_new(&preconditioner, 8, usize::MAX).unwrap();
        let mut output = vec![0.0; rhs8.len()];
        for _ in 0..3 {
            preconditioner
                .apply_compatible_batch_into(&rhs8, &mut output, 8, &mut workspace, context)
                .unwrap();
            assert_eq!(bits(&output), serial[7].1);
        }
    }
    if reference != team_free {
        // Only the scalar fixed-chunk centering of one large coarse component
        // may differ from the team-free recurrence, and only at rounding.
        let scale = rhs1.iter().fold(0.0_f64, |s, x| s.max(x.abs()));
        for (a, b) in reference.iter().zip(&team_free) {
            let (a, b) = (f64::from_bits(*a), f64::from_bits(*b));
            assert!((a - b).abs() <= 1e-10 * scale.max(1.0), "{a} vs {b}");
        }
    }
}

fn bits_of_lane(values: &[u64], columns: usize, lane: usize) -> Vec<u64> {
    values.iter().skip(lane).step_by(columns).copied().collect()
}

/// Thresholds low enough that dense levels hold row operators and sparse
/// levels from 512 vertices on hold restriction rows (and use the scalar
/// fixed-chunk centering of their single component).
fn small_thresholds() -> ParallelOptions {
    ParallelOptions {
        min_parallel_len: 64,
        reduction_chunk_size: 64,
        ..ParallelOptions::default()
    }
}

#[test]
fn dense_team_applications_reproduce_serial_bits_at_every_width() {
    check(&worker_firm(6_000, 900), small_thresholds(), true);
}

#[test]
fn sparse_team_levels_restrict_rows_and_reproduce_serial_bits() {
    check(&sparse(9_000, 1), small_thresholds(), false);
}

#[test]
fn disconnected_team_applications_reproduce_serial_bits() {
    // Several components: centering keeps the serial compensated sums, so
    // the scalar team cycle equals the team-free cycle too.
    let graph = sparse(3_000, 3);
    check(&graph, small_thresholds(), false);
    let preconditioner = CmgPreconditioner::build(
        &graph,
        CmgOptions {
            direct_threshold: 64,
            ..CmgOptions::default()
        },
    )
    .unwrap();
    let host = pool(4);
    let team = Team::new(&host, 4, small_thresholds()).unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, team).unwrap();
    let rhs = interleaved(&graph, 1);
    assert_eq!(
        scalar(
            &preconditioner,
            &rhs,
            ApplyContext {
                plan: Some(&plan),
                team: Some(team),
                cancel: None,
            }
        ),
        scalar(&preconditioner, &rhs, ApplyContext::default())
    );
}

#[test]
fn without_fixed_chunk_centering_scalar_team_cycles_are_serial_bits() {
    // A reduction chunk too large for any level: no team centering and no
    // operator-free restriction rows; operator levels still run on the team.
    let options = ParallelOptions {
        min_parallel_len: 64,
        reduction_chunk_size: usize::MAX,
        ..ParallelOptions::default()
    };
    let graph = worker_firm(6_000, 900);
    let preconditioner = CmgPreconditioner::build(&graph, CmgOptions::default()).unwrap();
    let host = pool(4);
    let team = Team::new(&host, 4, options).unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, team).unwrap();
    assert!(plan.operator_count() > 0 && plan.restriction_count() > 0);
    let rhs = interleaved(&graph, 1);
    assert_eq!(
        scalar(
            &preconditioner,
            &rhs,
            ApplyContext {
                plan: Some(&plan),
                team: Some(team),
                cancel: None,
            }
        ),
        scalar(&preconditioner, &rhs, ApplyContext::default())
    );
}

/// Pruned coarse isolates: restriction rows from partial transfers and the
/// team prolongation of rows with and without a coarse entry.
#[cfg(feature = "experimental-components")]
#[test]
fn pruned_transfers_run_on_the_team_with_serial_bits() {
    use cmg::{BuildContext, BuildOptions, ComponentBuildOptions};
    // A sparse body plus many pendant pairs whose aggregates become coarse
    // isolates, so coarse levels retire vertices.
    let body = 6_000;
    let pendants = 1_500;
    let mut edges: Vec<(usize, usize, f64)> = (1..body)
        .map(|v| (v - 1, v, 0.5 + (v % 5) as f64 / 3.0))
        .collect();
    for p in 0..pendants {
        let (a, b) = (body + 2 * p, body + 2 * p + 1);
        edges.push((a, b, 2.0 + (p % 3) as f64));
    }
    let graph = Laplacian::from_edges(body + 2 * pendants, edges).unwrap();
    let preconditioner = CmgPreconditioner::build_with(
        &graph,
        BuildOptions {
            hierarchy: CmgOptions {
                direct_threshold: 64,
                ..CmgOptions::default()
            },
            components: ComponentBuildOptions {
                prune_coarse_isolates: true,
                preserve_unpruned_stopping: true,
                factor_terminal_components: true,
            },
        },
        BuildContext::default(),
    )
    .unwrap();
    assert!(
        preconditioner
            .hierarchy()
            .levels()
            .iter()
            .any(|level| level.pruned_transfer().is_some())
    );
    let host = pool(4);
    let rhs8 = interleaved(&graph, 8);
    let rhs1: Vec<f64> = rhs8.iter().step_by(8).copied().collect();
    let serial8 = batch(&preconditioner, &rhs8, 8, ApplyContext::default());
    let serial1 = scalar(&preconditioner, &rhs1, ApplyContext::default());
    for threads in [2, 4] {
        let team = Team::new(&host, threads, small_thresholds()).unwrap();
        let plan = ParallelCmgPlan::build(&preconditioner, team).unwrap();
        assert!(plan.restriction_count() > 0);
        let context = ApplyContext {
            plan: Some(&plan),
            team: Some(team),
            cancel: None,
        };
        assert_eq!(batch(&preconditioner, &rhs8, 8, context), serial8);
        assert_eq!(scalar(&preconditioner, &rhs1, context), serial1);
    }
    // Exact zeros stay zero.
    let team = Team::new(&host, 4, small_thresholds()).unwrap();
    let plan = ParallelCmgPlan::build(&preconditioner, team).unwrap();
    let zeros = vec![0.0; rhs8.len()];
    let context = ApplyContext {
        plan: Some(&plan),
        team: Some(team),
        cancel: None,
    };
    assert!(
        batch(&preconditioner, &zeros, 8, context)
            .iter()
            .all(|&bits| f64::from_bits(bits) == 0.0)
    );
}

/// The plan's validation still rejects a plan of another hierarchy.
#[test]
fn restriction_rows_belong_to_their_hierarchy() {
    let graph = sparse(9_000, 1);
    let options = CmgOptions {
        direct_threshold: 64,
        ..CmgOptions::default()
    };
    let first = CmgPreconditioner::build(&graph, options).unwrap();
    let second = CmgPreconditioner::build(&graph, options).unwrap();
    let host = pool(2);
    let team = Team::new(&host, 2, small_thresholds()).unwrap();
    let plan = ParallelCmgPlan::build(&first, team).unwrap();
    assert!(plan.restriction_count() > 0);
    let rhs = interleaved(&graph, 1);
    let mut workspace = second.try_workspace().unwrap();
    let mut output = vec![0.0; rhs.len()];
    assert!(
        second
            .apply_compatible_into_with(
                &rhs,
                &mut output,
                &mut workspace,
                ApplyContext {
                    plan: Some(&plan),
                    team: Some(team),
                    cancel: None,
                },
            )
            .is_err()
    );
}
